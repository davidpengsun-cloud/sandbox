# Installing the KGE pilot, kit @VERSION@

This guide installs the KGE pilot from its release kit alone: the zip, the image tarballs and this guide. Nothing else is needed, and nothing here is fetched from anywhere but the places named. The pilot serves a governed knowledge graph to a page and an API: people sign in with a username and a password, ask questions, and get figures that cite the certified sources they rest on.

It installs two ways, which hold the same parts:

| Mode | Services | Data features |
|---|---|---|
| Context service alone | `docker compose up -d app gcs` | Developer page, APIs, MCP and guides; use the portal for the Context workspace |
| Portal alone | `KGE_URL= PORTAL_CATALOGUE_FILE=/catalogue/catalogue.yaml docker compose up -d portal` | catalogue browse, search, catalogue Ask and portal-owned activity; preview, export and query are off |
| Together | `docker compose up -d` | KGE and portal features; table queries require `PORTAL_TABLE_QUERY=on`, previews require `PORTAL_TABLE_PREVIEW=on` |

The portal hosts the client’s ALTA home. Set `PORTAL_CLIENT_NAME` to the client’s display name alone, for example `Meridian` (not `Meridian ALTA`), to show “Meridian ALTA” and “Everyone at Meridian”. Unset or blank uses `Client`; surrounding whitespace is removed. The same value names KGE’s role guides and developer API reference. This setting does not change the client’s profile id. `PORTAL_PRODUCT_NAME` (default `ALTA`, also supplying KGE’s `PRODUCT_NAME`) and `PORTAL_PRODUCT_LONG_NAME` (default `ALTA: AI-powered Library for Trusted Assets`) set the product’s short name and long form when starting the containers. `PORTAL_PRODUCT_SUBTITLE` sets the Discover home subtitle; trimmed, unset or blank uses “Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.” `PORTAL_CONTEXT_ASK_TITLE` and `PORTAL_CONTEXT_ASK_SUBTITLE` set the Context Ask header’s title and subtitle; each is trimmed, and unset or blank uses “Ask a governed question” and “Certified definitions and figures, answered only from data you may read.” respectively. With the default settings, sign-in opens the ALTA home and its four apps: Certify is a stub that links out to the Power App, Context is the workspace inside the portal, Discover offers browsing, search, questions and access requests, and Cerebro (named `Cerebro` unless `PORTAL_CEREBRO_NAME` names it otherwise) chats over the graph you may read. The common bar switches apps. Context and Cerebro need the KGE service; Discover can run with its own catalogue and identity as below.

The header hides the product name beside a logo by default. To show it, set `PORTAL_LOGO_TITLE=on` in the Compose `.env` and recreate the portal, or set `portal_logo_title = "on"` in the pilot tfvars and apply the Terraform change. The setting takes `on` or `off`; an invalid value stops the portal and names it. When no logo is available the name remains visible, and the home link retains its accessible name in either setting.

Open the portal and choose **Context** in the app switcher, or open `/context` at the portal’s address and sign in with your email and password. Context’s Docs opens its end-user playbooks and connection help. The portal’s `/api/context/` backend serves onboarding, job status, review, quality, questions, maps and versions. The SQL check remains available through the API. KGE makes every permission decision as that person; the portal never supplies a principal in a request body. Apps and agents still use KGE’s own API and MCP with their own tokens.

The service’s `/` always shows the developer page. People use the portal’s `/context` workspace; the service also offers APIs, MCP, `/onboarding-guide` and `/docs`.


`PORTAL_KNOWLEDGE_HOME`, `PORTAL_APP_CERTIFY`, `PORTAL_APP_CONTEXT`, `PORTAL_APP_DISCOVER` and `PORTAL_APP_CEREBRO` default to `on`. Each takes `on` or `off`; a different value stops startup and names the setting. With the ALTA home off, exactly one app must be on or startup refuses and names the flags to change. Sign-in then opens that app, without the ALTA home or app switcher. An app switched off is shown as “not set up here” and its pages and API are unavailable. With no KGE service configured, Context is also treated as off: its page and backend are unavailable and settings return no Context MCP address. Cerebro needs a KGE service configured behind the portal, as Context does, but not Context itself: with its flag on and KGE configured it is set up whatever `PORTAL_APP_CONTEXT` says, and it may be a single-app install's one app. With no KGE service configured, Cerebro is treated as off. `PORTAL_CEREBRO_NAME` names Cerebro; unset or blank uses `Cerebro`. `PORTAL_CERTIFY_URL` sets the external Certify address; unset or empty leaves `[Power App link]`.

Compose passes these sixteen settings from the shell or its `.env`; recreate the portal container after changing them, and the app container when changing the display or product name. The pilot's tfvars take `portal_client_name` (default `Client`), `portal_product_name` (default `ALTA`), `portal_product_long_name` (default `ALTA: AI-powered Library for Trusted Assets`), `portal_product_subtitle` (default `Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.`), `portal_context_ask_title` (default `Ask a governed question`), `portal_context_ask_subtitle` (default `Certified definitions and figures, answered only from data you may read.`), `portal_logo_title`, `portal_knowledge_home`, `portal_app_certify`, `portal_app_context`, `portal_app_discover`, `portal_app_cerebro`, `portal_cerebro_name` (default `Cerebro`), `portal_certify_url`, `portal_table_preview` and `portal_table_query`; the app flags default to `on`, the logo title and table flags to `off`, and a null or empty URL is not passed. Applying those settings creates a portal revision; changing the display or product name also creates a KGE service revision. Neither installation needs an image rebuild. The portal's Cloud Run timeout is 300 seconds, above Context's 285-second proxy budget.

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
| KGE's developer page, API and MCP | service `app` | a Cloud Run service per client, `kge-<client>` |
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
                                          skills/ (five knowledge-engineer skills, versioned with this kit);
                                          plugin/ (those skills, the pack check and the kge helper);
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
  docs/POSTURE.md                         the telemetry and model posture: the image's defaults, each with its proving tests
  SHA256SUMS                              every file above
```

## Prerequisites

- Docker, with Compose v2 (`docker compose version`), on linux/amd64.
- Bash 4.4 or later (the `kge` helper requires it), Python 3 (standard library only), `unzip`, `curl` and `sha256sum`. The system Bash on macOS may be older; put a current Bash on PATH.
- For Google Cloud, also: the Google Cloud CLI (`gcloud`), signed in as the project's owner (Cloud Shell has it), and Terraform 1.17.0.
- A browser.

When building a kit on an arm64 machine, pass `BUILD_FLAGS=--platform=linux/amd64`; the kit requires a linux/amd64 image.

## Image vulnerability scans

Every published kit scans the shipped KGE and portal runtime images with Trivy for HIGH and CRITICAL vulnerabilities in both operating-system and language packages. A finding with an available fix blocks publication. Findings without a fix also block publication unless an unexpired exception accepts that exact CVE and package. The scan uses the vulnerability database available when the kit is built; a later database may report new findings.

The full JSON reports are `images/kge-@VERSION@.trivy.json` and `images/kge-portal-@VERSION@.trivy.json`, beside each image's `.sbom.cdx.json` bill of materials. In each report's `Results[].Vulnerabilities`, `VulnerabilityID` names the CVE, `PkgName` the affected package, `Severity` its rating, and `FixedVersion` the available fix (empty when none is available). `Metadata` identifies the scanned image. These reports and `images/scan-exceptions.json` are covered by `SHA256SUMS`.

Read `images/scan-exceptions.json` alongside the reports. Each entry gives `cve`, `package`, `reason` for temporary acceptance and `expires` as a UTC calendar date. Entries expire within 30 days; on that date the kit scan fails until the exception is reviewed. An exception never permits a finding with a fix. Installers should review the reasons and dates under their own security policy before loading the shipped images.

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
   kge setup-client CLIENT=meridian ASKER=analyst_enterprise BASELINE=baselines/issue-474.json
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

   Before applying, confirm an Enterprise or Enterprise Plus QUERY assignment for the project's GQL queries. If the kit must supply it, set `bigquery_create_reservation = true` in `$KIT/terraform/pilot/example.tfvars`. The default is false. When upgrading an older kit that already manages the reservation, set it true to retain that reservation; false plans its removal. Enabling it creates billed slot capacity; review [Long figure questions](#long-figure-questions) for the slot maximum and separate charges.

   ```sh
   cd $KIT/terraform/pilot
   terraform init -backend-config="bucket=$PROJECT-tfstate" -backend-config="prefix=kge/pilot"
   VARS=(-var-file=example.tfvars -var="project_id=$PROJECT" -var="billing_account=$BILLING" -var="region=$REGION" -var="github_repository=$REPO" -var="alert_email=$ALERTS")
   terraform apply "${VARS[@]}" -target=module.secrets
   head -c 36 /dev/urandom | base64 | tr -d '\n' | gcloud secrets versions add kge-meridian-session-key --data-file=-
   printf '{}' | gcloud secrets versions add kge-meridian-passwords --data-file=-
   terraform apply "${VARS[@]}"
   ```

   This creates, for Meridian: the store bucket, its landing bucket and the tmp bucket (all private), the tmp bucket's grants, among them the predefined `roles/storage.bucketViewer` (`storage.buckets.get` and `storage.buckets.list`, no object permission; a BETA-stage role in Google's list), which a BigQuery load from the tmp bucket needs, a Cloud Run service and a Cloud Run job, `kge-meridian`, each with its own service account, the discovery portal's Cloud Run service, `kge-portal-meridian`, running as the `kge-meridian` service's account (it adds no account, dataset or grant; it makes its own `_portal_*` tables in the client's existing warehouse dataset when it first starts), the image repository `kge`, BigQuery's datasets (empty for now; no reservation by default), the budget and its alerts. The page is public and signs people in itself.

   **A private page.** Where your organisation's domain-restricted sharing (`constraints/iam.allowedPolicyMemberDomains`) refuses `allUsers`, the apply's grant of the page to `allUsers` fails. Add `-var="public=false"` and the members who may reach the page, such as `-var='invokers=["domain:example.com"]'`, to `VARS` and apply again: Cloud Run then lets in only those members, each with a Google identity token, and the page still signs people in itself. A proxy that calls the private page sends the Google identity token (`gcloud auth print-identity-token`) in `X-Serverless-Authorization: Bearer <token>`, which Cloud Run checks, and leaves `Authorization` and `Cookie` to the page's own sign-in: the page reads `Authorization: Bearer` as its own sign-in token, ahead of the session cookie, so a Google token there is an unknown token, answered 401. The uptime check then counts the 403 Cloud Run gives Google's checkers as up, which proves only that Cloud Run answers at the page's address, not that the page runs: Cloud Run refuses the checkers before any instance is reached. An external agent is named in that same `invokers` list: [Cloud Run invokers for an external agent](#cloud-run-invokers-for-an-external-agent).

### Cloud Run invokers for an external agent

With `public = false`, Cloud Run admits a caller only when that caller holds `roles/run.invoker` on `kge-<client>`. Add each caller to `invokers` and apply. Replace every placeholder below with the installation's own value. Do not commit a project id, a project number, an organisation id, an agent id or a service URL.

An Agent Studio agent (`aiplatform.googleapis.com`) calls as one principal identity. Name it exactly:

`principal://agents.global.org-<ORG_ID>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUMBER>/locations/global/agents/<AGENT_ID>`

A set of such identities uses `principalSet://` with the same kind of path. A Conversational Agents (Dialogflow) OpenAPI tool calls as the Dialogflow service agent:

`serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com`

Enabling an API does not create its service agent. Create the identity first, or the invoker binding has no member to attach. For Conversational Agents the API is `dialogflow.googleapis.com`; use whichever API was just enabled when its service agent must hold `roles/run.invoker`:

```sh
gcloud beta services identity create --service=dialogflow.googleapis.com
```

Then apply with `public = false` and those members, for example:

```sh
VARS+=(-var="public=false" -var='invokers=["principal://agents.global.org-<ORG_ID>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUMBER>/locations/global/agents/<AGENT_ID>","serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com"]')
```

`user:`, `group:`, `serviceAccount:` and `domain:` members stay valid. `allUsers` and `allAuthenticatedUsers` are refused. The Google identity token still goes in `X-Serverless-Authorization`. The person's KGE token goes in `X-KGE-Authorization: Bearer <token>` when Cloud Run is already using `Authorization`. The kit zip's `openapi/kge-tools.openapi.yaml` is a reference OpenAPI 3.0.3 tool spec for `POST /context`, `POST /ask`, `POST /search` and `POST /data/query` with that header declared. Have the agent send `"compact": true` on `POST /ask` and `POST /context`: the answer, figures, definitions and citations stay, and the SQL, the run trace and the plan internals are left out, so a low-code agent's turn stays within its token limit.

For Vertex AI Agent Builder / Dialogflow CX Agent Studio on the same project, or instead of running `gcloud beta services identity create --service=dialogflow.googleapis.com` and listing the Vertex AI and Dialogflow service agents in `invokers` by hand, set `-var="agent_studio=true"` in `VARS` (remove any manual `gcp-sa-dialogflow` or `gcp-sa-aiplatform` entries from `invokers` when switching to `agent_studio=true`): Terraform then enables `dialogflow.googleapis.com` and `discoveryengine.googleapis.com`, adds `dialogflow.googleapis.com` and `<region>-dialogflow.googleapis.com` to `egress_allowed`, creates the Dialogflow service identity, and (when `public = false`) grants `roles/run.invoker` on `kge-<client>` to both the Vertex AI and Dialogflow service agents alongside `invokers`. Dialogflow CX's `ID_TOKEN` call reaches Cloud Run through Google's serverless front end, so `kge-<client>` requires `INGRESS_TRAFFIC_ALL` (the module default), or Service Directory private network access if Cloud Run is restricted to `INGRESS_TRAFFIC_INTERNAL_ONLY`.

Once applied, provision or rotate the Studio agent (`ALTA Wealth NNA Assistant`), its OpenAPI tool (`ID_TOKEN` service-agent authentication plus `X-KGE-Authorization`) and its start playbook in `global` and `<region>` with `kge studio`. `PERSONA=<email>` is required and must be a query-only persona (`admin = false`, `writes_terms = false`, and `processes = ["querying"]`, such as an analyst, finance, or advisor persona) so the shared tool never embeds an operator's, steward's, glossary steward's, business team's, or knowledge engineer's token:

```sh
./scripts/kge studio ENV=gcp/<client> PERSONA=advisor_wealth.<client>@example.com
```

The embedded `X-KGE-Authorization` bearer token from `POST /session/token` has a 15-minute (`900` s) TTL ([Tokens for an agent](#tokens-for-an-agent)), and `kge studio` prints `persona=<email>` and `ttl=900s(exp=<epoch>)` (reading `expires_in` from `/session/token`) on each run. To keep the Studio tool authenticated continuously, the operator (or a supervised runner such as `systemd` with `Restart=always`, `cron`, or Cloud Scheduler holding `roles/run.invoker` on `kge-<client>` and Dialogflow admin access on `<PROJECT_ID>`) rotates the token in place every 10 minutes (`<= 600` s, before the 900-second expiry) for a single `LOCATION=<loc>` and `AGENT=<AGENT_ID>` using `EVERY=600` and a private mode-`0600` `PASSWORDS_FILE=<path>` (such as `~/.config/kge/passwords/gcp/<client>-latest`):

```sh
./scripts/kge studio ENV=gcp/<client> PERSONA=advisor_wealth.<client>@example.com LOCATION=global AGENT=<AGENT_ID> PASSWORDS_FILE=~/.config/kge/passwords/gcp/<client>-latest EVERY=600
```

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
gcloud run services update "$PORTAL_NAME" --region="$REGION" --service-account="$PORTAL_SA" --update-env-vars="PROFILE=$CLIENT,PORTAL_BACKEND=bigquery,BIGQUERY_PROJECT=$PROJECT,BIGQUERY_DATASET_PREFIX=$PREFIX,KGE_URL=$KGE_URL,KGE_AUDIENCE=$KGE_URL,KGE_CALLER_AUDIENCE=$KGE_RUN_URL,TRACE_PROPAGATE_TO=$KGE_URL"
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

`schema.yaml` names the warehouses, tables, columns, types and keys, with parent tables before children. Every column needs a description; BigQuery allows at most 1,024 characters. Each CSV's header names its columns in schema order; empty fields are NULL. Documents require `title` and `kind` in front matter. Optional keys are `unit`, `owner`, `version`, `published_at`, `reviewed_at`, `superseded_by`, `certification` and `planted`. A unit, if supplied, must be a schema warehouse. The sample's uncertified documents carry only `title`, `kind` and `planted`; `planted` is a list of strings holding the sample's test tags and may be kept or removed. Golden v2 uses an object with `version: v2` and an `items` list; a bare list is v1 and cannot pass the margin gate. An optional `document_order` list in `schema.yaml` names every `docs/<shelf>/<id>.md` path exactly once; the generated sample uses it to preserve its landed release identity. Keep the list in step when adding or removing documents, or omit it to use shelf and filename order. Start with the downloaded sample fixture or supply the client's own files.

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

The job compares file contents, not times or sizes, and stages an exact mirror of the people-owned files. Files absent from the upload are removed on success, including stale catalogues and includes. `pack.lock`, `baselines/`, `versions/`, `proposals/`, `units/` and `onboard/` are job- or page-owned and left alone; uploaded copies are ignored. The release job writes `baselines/examples.json` after each release: the golden questions the released rules certify, each with its plan, which an AI-Generated answer's prompt reads as verified examples. Dotfiles are ignored too, including Mac `._*.md` files already in the live fixture; a local store may hide them from the ignored list. The upload digest is recorded with the run. Successful publication writes fixture files, includes and catalogue before `pack.yaml`; Cloud Storage has no transaction across objects, so this is an ordered publication, not a multi-object atomic write. A failure before publication restores the staged inputs and publishes only its diagnostic record.

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
| `F12` | a test item a question shape cites (`shape_tests`) is not an item of its set file, has fewer than three alternate phrasings, has a partial or malformed approval of its reference SQL, or carries reference SQL with no approval while a shape that tests it is above `specify` |
| `F13` | a code a glossary term declares is not a value of its column in the fixture's rows, or a term's `broader` term disagrees with the rows: a row with the term's code has another code, or none (NULL), in the broader term's column, on the same row or through the declared join between their tables (where the broader term is coded on several columns of one table, a row holding its code in any of them agrees, and a row is reported once; where both terms are coded on one column of the table the broader term's columns are on, as a member and its parent are on a dimension's key, the row reached is the member's own, so its parent row is compared, through that table's join to itself), or the two terms' rows cannot be compared because no hierarchy or key join between their tables equates their columns (a parent-child table needs its join to itself, the alias naming the parent row). A row whose join key is NULL has no broader code. Numbers, booleans, dates and timestamps compare by value |
| `A01` | an access-table row holds a role the pack does not define |
| `B01` | the role the baseline asked as no longer exists |
| `B02` | the baseline cannot be read, uses another graph store, or has no comparable declared set |

A cited test item whose reference SQL is not yet approved, while every shape that tests it is at `specify`, is a draft, not a finding: it is printed as `onboard: warning: F12 <file>:<entry>: draft: ...` (`check-pack: warning:` in the pack check) and refuses nothing.

A unit the fixture holds no rows for, such as one onboarded through the page, is not checked against rows at upload: `F13` prints `onboard: not checked here: F13 <unit>: ...` (`check-pack: not checked here:` in the pack check) and refuses nothing. Onboarding checks every unit's codes and `broader` terms again at its compile step, against the unit's live warehouse version, with one bounded query for each coded column of a term and the broader term's columns it reaches, and one for each coded column not compared on the same row as another, beside a description of each coded table; a finding there is listed as `onboard: refused: F13 ...`, then `onboard: refused, <n> finding(s): nothing was loaded`, with exit 2 at step `compile`. A pack that declares no codes sends no query and prints nothing.

Pack syntax findings may also be listed. Both baseline findings name the remedy: `fix: release records a new baseline on the released graph (or run baseline); or clear it with setup-client BASELINE=`. Clearing a baseline removes that comparison, so review the reason before doing it; the pack's gates still apply. To clear it without changing access:

```sh
"${JOB[@]}" --args="setup-client,CLIENT=$CLIENT,BASELINE="
```

On the page, read **Candidate changes** and the A/B report before sign-off (the page shows all of these; the job's log prints only the candidate hash, ignored files, missing declarations and whether checks are repeated): schema changes, estate rebuild reason, **Reloaded the same model**, ignored files, declarations to add, upload digest, checks repeated. A failed gate is checked again on the next onboard. Only unchanged inputs with a passing gate give `onboard: current already` or `onboard: candidate already loaded and checked=<hash>`.

Set `HASH` to the checked hash and `STEWARD` to the client's steward. The steward normally signs off on the page and the knowledge engineer then releases there; the job operator's break-glass equivalent is:

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

Use the knowledge engineer's **Roll back to the previous release** action, or:

```sh
"${JOB[@]}" --args="release,PREVIOUS=yes"
gcloud run services update "${SERVICE_NAME:-kge-$CLIENT}" --project="$PROJECT" --region="$REGION" --revision-suffix="release-$(date +%s)"
```

This also restores the previous release at the same hash. `release HASH=<hash>` can select a retained release; it cannot rebuild a pruned snapshot. A rollback keeps the live access table and passwords unchanged.

After every successful job release, rollback included, the job records a baseline only after automatic eval post passes, on the released graph using rules, with no comparison to the old baseline. It saves `baselines/<graph store>-<UTC time>.json`, updates the profile and prints `release: baseline re-recorded: baselines/<file>, from run <id>; the profile names it, people and asker kept`. No `setup-client` step follows. If recording fails, the release has already succeeded: the command exits nonzero and says `release: <hash> is released; recording its baseline failed: <reason>: run baseline`. Fix the cause and run the `baseline` job; it records and updates the profile in the same way.

A set changed since that baseline advances its version, keeps each unchanged question's id and gives a new question a new id. Keep the pack under version control: its history is the record of what changed and why. A set declares no change and no file digest; a `changes_from` that an older set still holds is ignored, so an upgraded install needs no edit to its sets. Update the corresponding `tests.sets` version. The gate compares every margin score, including citations, on each set at the version the baseline ran; a set changed since is reported by its counts alone and its margin is not checked until the baseline is re-recorded. If no set is at the baseline's version, the check stops with B02 and writes no GATE.

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

Roles belong to the pack (`access.roles`); the live access table binds each person to one or more role ids. Processes are platform-declared capabilities: `drafting`, `building`, `improving` and `accepting`, alongside `querying` and `certification`. A pack grants those processes to roles; a role label alone grants nothing. The knowledge engineer uploads and drafts, the business team or a steward accepts, the steward certifies, the knowledge engineer runs checks, the steward signs off, and the knowledge engineer releases and rolls back. The platform engineer installs the service and assigns people; installation grants no KGE role.

Four people take part: the knowledge engineer, the business team, the steward and the end user. The page checks each step against the role of the person signed in, using the person's live access-table grants:

| Step | Who does it | Role the page checks | Page route | Job command | Role source |
|---|---|---|---|---|---|
| Land a unit: its tables, its sources, its rows | Knowledge engineer | `drafting` | `POST /onboard/tables`, `POST /onboard/source`, `POST /onboard/rows` | page only | live access table against the served pack's roles |
| Draft | Knowledge engineer | `drafting` | `POST /onboard/draft` | page only | live access table against the served pack's roles |
| Accept drafted items, answer open requests | Business team or steward | `business` | `POST /onboard/accept` | page only | live access table against the served pack's roles |
| Certify | Steward | `steward` | `POST /onboard/certify` | page only | live access table against the served pack's roles |
| Build and gate | Knowledge engineer | `building` | `POST /onboard/run`, step `checks` | `onboard` | live access table against the served pack's roles |
| Read the margin check | Knowledge engineer | `building` | `GET /onboard/margin` | API | live access table against the served pack's roles |
| Improve | Knowledge engineer | `improving` | `POST /onboard/run`, step `improve` | `improve` | live access table against the served pack's roles |
| Sign off | Steward | `steward` | `POST /onboard/run`, step `sign-off` | `sign-off PRINCIPAL=<steward>` (knowledge engineer's break-glass access) | live access table against the candidate's `access/roles.json` |
| Release | Knowledge engineer | `releasing` | `POST /onboard/run`, step `release` | `release` | live access table against the candidate's `access/roles.json` |
| Roll back to the previous release | Knowledge engineer | `releasing` | `POST /onboard/rollback` | `release PREVIOUS=yes` | live access table against the current released roles |
| Roll back through the run control | Knowledge engineer | `releasing` | `POST /onboard/run`, step `rollback` | `release PREVIOUS=yes` | live access table against the current released roles |
| Ask | End user | `matrix("ask")` | `POST /ask`, `POST /context`, `POST /answer`, `POST /agent` | page or API only | live access table against the served graph roles |
| Give feedback on an answer | End user | `matrix("feedback")` | `POST /ask/feedback` | page or API only | live access table against the served graph roles |

- `business`: a person whose role has `accepting`, or, for compatibility, writes `terms` and `mappings` without `certification`; in the example, `business_team`.
- `steward`: a person whose role performs `certification`; in the example, `steward_glossary`, `steward` and `admin`. Stewards accept through the role matrix's Steward row without an `accepting` grant; certifying and signing off still need `certification`.
- `releasing`: a person whose role has `building`; in the example, `knowledge_engineer`. Separation of duties: stewards certify and sign off but do not release; engineers build and release but do not certify.
- `drafting`, `building`, `improving`: the corresponding process in the pack role. `knowledge_engineer` has all three and `querying`, with no acceptance writes or `certification`. The business team reads, accepts and answers requests; it has none of these automated processes. Stewards certify and sign off; they do not run checks or improvements, release or roll back.
- `participant`: the business team, a steward, or a role with any of those three processes, which opens the page's onboarding panel.
- `needs("querying")`: a role with `querying`, which answers within the units it reads.
- `matrix("ask")`, `matrix("feedback")`: the same `querying` check, then the role matrix's row: a business user, a steward or a knowledge engineer. Only a knowledge engineer's answer carries its plan, SQL and trace; anyone else's is compact.

In front of each role above, the API checks the Cerebro role matrix and writes a role-audit entry (`GET /access/role-audit`): accepting, certifying and signing off are on the stewards' row (`steward`, `steward_glossary`, `business_team`), but certifying and signing off still need `certification`, so `business_team` accepts and curates but does not certify or sign off; drafting, building, the margin read, improving, release and rollback are for knowledge engineers, and the steward queue and its gaps for stewards, which `business_team` reads too, while deciding a queue item needs `certification` (`steward_decide`), so `business_team` decides nothing (open in #503); an onboarding MCP tool checks the same row as its route. An `admin` holding `certification` is refused certifying, signing off, releasing, rolling back and curating, and `steward` and `steward_glossary` accept, never certifying their own acceptance.

Sign-off and release of a candidate check the live access table against that candidate's `access/roles.json`, on the page and in the job alike. A later pack edit cannot supply its steward permission. Rollback checks the live access table against the current released roles; stored onboard and improve requests use the same live table and released-role checks.

The business team answers open requests with the knowledge engineer, who uploads any revised sources; a request also closes when its item is certified. Of the job commands, `sign-off` checks `PRINCIPAL`; `onboard`, `improve` and `release` name no person, so whoever may execute the job may run them: on Google Cloud, whoever IAM lets execute it; under Compose, whoever runs `docker compose`.

Whoever runs the KGE job on Google Cloud, or `docker compose run job` locally, is a knowledge engineer. Running the job by hand can sign off with `sign-off PRINCIPAL=<steward>` and `release` without the page. This is the knowledge engineers' break-glass power, recorded in Cloud Run's execution log. The page remains the governed route: its engineer principal cannot sign off. Signed page requests that remove the job operator's power are later hardening, outside this change.

The knowledge engineer uses its own principal, `knowledge_engineer.meridian@example.com` in the invented Meridian people. HTTP and MCP apply the same process checks. Accept, certify, sign-off and release require their human roles and have no MCP tools. The platform engineer owns installation through Google Cloud IAM and holds no KGE role by default; neither role implies the other. Older packs still compile and serve; their roles need explicit `drafting`, `building` and `improving` grants to use those automated endpoints.

Improvement is a workflow of its own, not a step of onboarding: it proposes (`improve`), the business team accepts the proposal, the steward certifies it, its candidate is built and checked, the steward signs it off and the knowledge engineer releases it, under the same roles as the table's.

### Manual and assisted routes

The manual commands in the roles table remain the reference route. After unzipping the kit, `skills/` contains five folders, each with a `SKILL.md`: `land-and-draft`, `build-and-gate`, `improve`, `ask` and `author-pack`. Load the chosen skill into the assistant connected to this installation over HTTP or MCP. Keep the skills with the kit version they came from. They require no additional server installation. `author-pack` writes pack files and runs the pack check before upload. It does not call the API.

The same five skills, the pack check and the `kge` helper are also in `plugin/` at the kit root. The portable way to load that folder is the one each product documents for a plugin you were given as files, not a marketplace name.

Claude Code does not install a folder with `claude plugin install`. That command takes `name@marketplace`. Load the kit folder for a session, which is the supported directory mechanism:

```text
claude --plugin-dir <kit>/plugin
```

In that session, `claude --plugin-dir <kit>/plugin plugin list` shows the plugin, and `claude --plugin-dir <kit>/plugin plugin details kge` lists its skills: `ask`, `author-pack`, `build-and-gate`, `improve` and `land-and-draft`. `claude plugin validate <kit>/plugin` only checks the manifest. It does not load the plugin or list its skills.

GitHub Copilot CLI installs the same folder and keeps it:

```text
copilot plugin install <kit>/plugin
copilot plugin list
copilot skill list
```

`plugin list` names `kge`. `skill list` names the five skills. In an interactive session the same list is `/skills list`. A session can also load the folder without installing it: `copilot --plugin-dir <kit>/plugin plugin list`.

`plugin/bin/check-pack` is the pack check. `plugin/bin/kge` is the helper. The check reads the pack and does not build a graph.

The image command is `check-pack`. Name the pack folder with `PACK=`; an omitted `PACK` checks the installation's profile, and an image with no profile registry refuses. Mount the folder and pass the path as an argument. `PACK` on its own, only in the environment, is not read. A blank `PACK=` is refused. `plugin/bin/check-pack` takes the same `PACK=<folder>` argument, or `--pack <folder>`. The helper's `check-pack` accepts that argument on Bash 3.2; its other commands need Bash 4.4:

```text
docker run --rm -v <folder>:/pack kge:@VERSION@ check-pack PACK=/pack
docker compose run --rm -v <folder>:/pack job check-pack PACK=/pack
```

The folders follow the Agent Skills open standard. Copy each chosen folder from the kit's `skills/` into one of these locations; keep each folder whole and its name unchanged. Locations verified against the vendors' documentation on 2026-10-25:

| Agent | In a repository | Personal |
|---|---|---|
| GitHub Copilot (CLI, VS Code agent mode, cloud agent) | `.github/skills/`, `.claude/skills/` or `.agents/skills/` | `~/.copilot/skills/` or `~/.agents/skills/` |
| OpenAI Codex (CLI, IDE extension, app) | `.agents/skills/` | `~/.agents/skills/` |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |

The `.agents/skills/` location serves Copilot and Codex alike. For example, copying `ask` there gives `.agents/skills/ask/SKILL.md`.

Connect the agent to this installation's streamable HTTP MCP servers: `land-and-draft`, `build-and-gate`, `improve` and `ask` use `/mcp/onboard`; `ask` also uses `/mcp/answer`, `/mcp` and `/mcp/identity`. `author-pack` does not. Use the person's own bearer token from `POST /session/token`, following [Tokens for an agent](#tokens-for-an-agent). Each agent has its own MCP configuration format; supply the server URL and header, for example:

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

The pilot's configuration plans and applies against stand-in providers, creating nothing, from the zip alone, with Terraform 1.17.0 (`terraform init` fetches `hashicorp/google` 8.11.0, which `terraform/pilot/.terraform.lock.hcl` holds, from the registry or from a mirror that `TF_CLI_CONFIG_FILE` names):

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

Search and Ask select KGE by default when it is configured, otherwise the portal's own tables. `PORTAL_SEARCH_PROVIDER=portal` selects portal Search explicitly; its `none` value remains an older alias for `portal`. Ask starts at `PORTAL_ASK_PROVIDER` in the `kge` → `portal` → `none` chain. The portal layer uses `PORTAL_ASK_MODEL` with `PORTAL_ASK_MODEL_PROVIDER` (`vertex` or `mock`) to answer from the readable catalogue within the page scope, without data or figures. Without a model, or if its call fails, it passes to `none`, the existing owner and fields rules. KGE Search uses certified terms, synonyms, lineage and governed document passages, without a generative model call. Portal Search searches its own catalogue. A timeout, network failure or server error may fall back with a visible notice. Authentication or access refusals never trigger fallback. Previously verified units remain usable for at most five minutes during an outage; no warehouse read uses that cache.

### API audiences

OpenAPI and MCP tool metadata label capabilities `external: knowledge` (context, retrieve, term mapping, catalogue and search), `external: query` (Ask, preview, export and query), or `external: identity` (sign-in, agent token, identity and readable units). Internal capabilities are tagged `internal: onboarding`, `internal: governance`, `internal: quality` or `internal: platform`. A tag explains the audience; it does not grant access. Human accept, certify, sign-off and release actions remain HTTP-only. The unauthenticated KGE `/health` and `/healthz` checks support GET and HEAD, report status, image version and, in `ask`, Ask's AI-Generated fallback in effect, its order and its model (`{"ai_fallback": "on", "order": "rules_first", "model": "<provider:model>"}`; `off`, `rules_first` and `none` without a model, `off`, `rules_first` and `unavailable` for a mistyped model setting, and `rules_first` whenever the fallback is `off`, since Ask then answers on the rules alone), and open neither graph nor warehouse. Use `/health` for uptime checks.

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

`kge eval pre` runs the candidate's margin check; its report is available at sign-off. The pre-release margin gate keeps its existing thresholds. Every release, including rollback, runs **eval post automatically** against the served graph, using the same questions, sets, asker role and scoring as that candidate's own pre-release results. Without a custom suite, post is automatic; there is no page button. A custom UAT suite can be run manually as described below. Anyone allowed to read releases can read the report saved with the release; it records that release's latest check.

| Comparison | Post-release rule |
|---|---|
| Retrieved items and order, SQL, figures, citations, withheld items, access rights and rules-path answers | Zero tolerance: any deterministic difference fails |
| Must-pass question set | Every required result must pass |
| Model-graded score | Report the difference and 95% bootstrap interval over questions, reusing three repeats; information only, never a release failure |

A failed post check fails the job loudly and raises a page banner saying **run kge rollback**. It never rolls back automatically. The release has already been served; investigate the report or run the rollback, which is checked too. Baseline re-recording starts only after eval post passes.

#### What an item is scored against

Every evaluation computes an item's expected result by one rule, before release (the margin check, the comparison of arms, the test drive's checks and the checks after a graph load) and after it (eval post and the baseline it records, the job's `eval` with or without `SUITE`, the API's evaluation request, the approved items of the client's further golden sets and the grounding evaluation):

| Item | How it is scored |
|---|---|
| Has approved reference SQL: `reference_sql`, with both `reference_sql_approved_by` and `reference_sql_approved_on` | Its reference SQL is run at the time of the evaluation on the runtime and data the answer used, through the same rendering and SQL checks as answers, and the answer is scored against what it returns |
| States a figure (a figure, rows, a table, parts or a ratio) with no approved reference SQL, answered on a runtime other than the reference warehouse | Shown as a draft and never scored: its stored expectation was set on the reference warehouse, not on that runtime |
| States a figure with no approved reference SQL, answered on the reference warehouse | Scored against its stored expectation, which was set on that warehouse |
| Asks for a figure and gives only `reference_sql` that is not approved | Scored against what that SQL returns on the reference warehouse when answered there; a draft when answered on any other runtime |
| Expects a behaviour, not a figure: a refusal, an abstention, citations | Scored on that behaviour on any runtime; its reference SQL, if any, is not run |
| Its approved reference SQL cannot run on the answering runtime (a table or column with no mapping there, a dialect the SQL cannot be rewritten for, access, or the warehouse refusing the query), or returns no figure or rows, or columns that are not the item's; or its expectation is parts or a ratio, which a query's result does not give; or the evaluation has no warehouse to run it on, as when it asks a running API | Reported as `expectation not available on <runtime>`, with the reason, apart from passes and failures. It is not a failure and never a known miss |

An item is asked on one runtime list and its expectation is computed on the same list: the run's `RUNTIMES` where it names one, else the item's or suite's `runtimes`, else the client's default. An answer that ran no query, such as a refusal or an abstention, names no runtime: it is scored on its behaviour, so a figure item answered that way fails on any runtime. A client whose tables are all served by the reference warehouse, as the Meridian example's are, gets the same scores from this rule as from its stored expectations.

The approved items of the client's further golden sets are part of the served release's evaluation and are reported in a table of their own, each item once in its own wording: every evaluation that asks them does so on the graph preset alone, and only from the sets whose units the person it asks as may read. Parity between versions stays a stored snapshot of answers: it detects any change from one version to the next and is not a source of truth.

Context's **Examples** mark an item a known miss where the release baseline records the default arm failing it against its own runtime's truth. They follow that baseline and change only when the next one is recorded: the release job re-records it after eval post passes, or the `baseline` job records it.

#### UAT suite format

Save a UTF-8 JSON file containing a suite object. A suite is a list of business questions with agreed expectations, separate from the automatic release comparison. Use the client's pack terms and reference schema names. A person approves each figure item's `reference_sql` and the figure it returns before that item counts towards acceptance. An agent drafting both is only testing itself.

| Field | Meaning |
|---|---|
| `suite_id`, `suite_version`, `title` | Suite identifier, nonempty version string (for example `"1.0"`) and descriptive title. Without `version`, `suite_version` makes the reader use `v1` internally. |
| `version` | Optional set version `v<n>`. A changed set advances it; version control holds what changed and why. A `changes_from` an older set still holds is ignored. |
| `defaults` | Shared item fields, such as `unit` and `tolerance`. Item fields override defaults; nested objects are replaced, not merged. |
| `questions` (or `items`) | List of explicit question objects. Both lists are read if supplied; use distinct ids throughout. |
| `id`, `question` | Distinct item identifier and business question text. |
| `category` | Optional descriptive metadata; does not change scoring. |
| `unit` | Warehouse/source id from the pack, such as `retail`; not a currency code. |
| `principal` | Optional querying role for an item; otherwise the run's configured asker. Reference truth uses that role's readable units too. |
| `expected_behavior` | Normalises to scoring `kind`: `figure`, `refusal`, `abstain`, `list` or `point`; `refuse` aliases refusal, and `clarify` or `abstention` alias abstain. An explicit `kind` takes precedence. |
| `reference_sql` | Read-only SQL for expected results, using reference table and column names. `${SCHEMA}.table` leaves schema routing to the warehouse adapter. Runs against the served warehouse with access and SQL checks, including the BigQuery byte cap when set. |
| `expected_total` | Optional fixed numeric figure when not deriving truth from SQL. A fixed figure or grouped rows can skip SQL truth resolution on the reference run; use SQL alone when deriving truth from the warehouse. |
| `tolerance` (or `tolerance_usd`) | Numeric absolute tolerance, or an object with `abs` and `rel`; a figure passes within the larger allowance. Set explicitly for the agreed precision. |
| `expected_rows` | Optional expected grouped rows, an object mapping each group key to its value. |
| `required_signals`, `forbidden_signals` | Optional lists of text signals that must appear, or must not appear, in the answer and its evidence. |
| `expect` | Scoring expectations, including `figure`, `tolerance`, `reference_sql`, `rows` and signal lists. Normalisation fills missing nested values from top-level fields. Use one location for `reference_sql`: execution reads top-level SQL first if both are present. `behaviour` can require an exact engine behaviour (for example `refuse`); `no_figure: true` forbids figures, parts and rows. |
| `reference_sql_approved_by`, `reference_sql_approved_on` | Optional: who approved the item's `reference_sql`, and the date, `YYYY-MM-DD`. Give both or neither, on the item, its template or the defaults. With both, the item's `reference_sql` is its expected result on whichever runtime answers it; without them, an item that states a figure is scored only on the reference warehouse and is a draft elsewhere ([What an item is scored against](#what-an-item-is-scored-against)). A test item a question shape cites needs both once a shape that tests it is above `specify`. |
| `also_asked_as` | List of alternate question strings; each inherits the expectation and gains id `<id>_alt1`, `<id>_alt2`, and so on. Expanded only in the object form. A test item a question shape cites has at least three distinct alternates, `also_asked_as` and `added_phrasings` counted together, none repeating the question or another. |
| `added_phrasings` | Optional list of further alternate question strings, numbered after the `also_asked_as` ones (`<id>_alt<n>`), so those keep their ids; each inherits the expectation. Expanded only in the object form. |
| `templates` | List of template objects expanded after explicit questions. |
| `id_prefix`, `question_template`, `matrix` | Template id prefix, question with `{variable}` placeholders, and a list of row objects supplying values. The template's `reference_sql` substitutes the same variables but preserves `${SCHEMA}`. Row fields override template fields. A row's `id` overrides the generated `<prefix>_001` id. |
| `runtimes` | Optional ordered list of pack runtime ids at suite or item level; `reference` names the reference runtime. An explicit run's `RUNTIMES` overrides the list. |

For example, this invented Meridian format sample expands to two retail revenue questions. It is not a client acceptance suite:

```json
{
  "suite_id": "meridian_format_sample",
  "suite_version": "1.0",
  "title": "Invented revenue format sample",
  "defaults": {"unit": "retail", "tolerance": {"abs": 0.01, "rel": 0}},
  "templates": [{
    "id_prefix": "REVENUE",
    "kind": "figure",
    "question_template": "Total retail revenue for {month} 2025",
    "reference_sql": "SELECT SUM(total_revenue_usd) FROM ${SCHEMA}.monthly_revenue WHERE month_start = DATE '{start}'",
    "matrix": [
      {"month": "April", "start": "2025-04-01"},
      {"month": "May", "start": "2025-05-01"}
    ]
  }]
}
```

Run the saved suite against the served client:

```text
kge eval post SUITE=<file> [ARM=…]
```

The brackets mark an optional argument; omit them when running the command. For example, `kge eval post SUITE=suite.json ARM=graph`. `SUITE` accepts a local file or a `gs://` URL readable by the job. The helper uploads local files to the configured store on GCP and Compose; on a local target it passes the absolute file path. `ARM` accepts `schema`, `catalog`, `graph`, `control`, `treatment`, a comma-separated list, or `all`. A custom run reports scores; it does not release content or replace the automatic post-release comparison. Inspect every result, including refused reference truth; a completed command alone is not acceptance.

## Configure

### The Compose install's `.env`

| Setting | Default | What it is |
|---|---|---|
| `CLIENT` | `meridian` | the client id: a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters; its profile and landing bucket take the same id |
| `KGE_PORT`, `PORTAL_PORT`, `GCS_PORT` | `8000`, `8090`, `4443` | KGE's, the discovery portal's and the emulator's ports on this machine, on 127.0.0.1 alone |
| `LADYBUG_MAX_DB_MB` | `128` | the most megabytes any one graph may take, a positive whole number; not in `.env.example`: add it only for a graph over 128 MB |
| `SESSION_SECRET` | none; required on Cloud Run with password sign-in | the key KGE's sessions are signed with, 32 characters or more; local and Compose runs generate a private run-state key when unset; the standalone portal does not use it |
| `SESSION_STATE_BACKEND` | `store` | shared sign-outs and personal tokens use the store; Compose uses this default and does not pass this setting from `.env` |
| `PORTAL_SYNC_TOKEN` | none: required off Cloud Run | the shared secret KGE and the portal use for whole-catalogue sync, 32 characters or more; the portal refuses to start in KGE mode without it; standalone mode does not need a KGE sync secret |

### Tokens for an agent

With `SIGNIN=passwords`, a person obtains an agent's bearer token by sending `POST /session/token` to the KGE service over HTTPS, with JSON `{"username":"<username>","password":"<password>"}` and `Content-Type: application/json`. Use that person's assigned sign-in details. This exchange shares the page sign-in's attempt limits: a refusal is 401, and a locked sign-in is 429 with `Retry-After` seconds.

The response is `{"access_token":"<token>","token_type":"Bearer","expires_in":900}`. Give the agent only `access_token`, and send it on every request to `/mcp`, `/mcp/answer`, `/mcp/assist`, `/mcp/onboard`, `/mcp/catalogue`, `/mcp/data`, `/mcp/search` or `/mcp/identity` as `Authorization: Bearer <token>` (or `X-KGE-Authorization: Bearer <token>`). On a private Cloud Run service, also send its Google ID token in `X-Serverless-Authorization: Bearer <Google ID token>`; that does not replace KGE sign-in.

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
| `ASK_PLAN_MAX_TOKENS`, `ASK_CLASSIFY_MAX_TOKENS`, `ASK_WORDING_MAX_TOKENS` | none | none; `app_env` sets it | image default: 16,384 tokens for Ask's planner and 4,096 for its glossary classifier and its wording, including thinking; each wins over `MODEL_<TIER>_MAX_TOKENS` and `MODEL_MAX_TOKENS` for its own call only ([The model](#the-model)). A value that is not a whole number above 0 stops startup, naming the setting, and so does a budget, of its own or taken from `MODEL_<TIER>_MAX_TOKENS` or `MODEL_MAX_TOKENS`, below twice the model's smallest thinking budget where Ask's model takes one: 256 tokens on Gemini 2.5 and 3, 1,024 on a Gemini Flash-Lite model. On OpenAI and Ollama a budget is sent only where one of these settings is set; unset, the model's own limit applies |
| `ASK_PLAN_THINKING_TOKENS`, `ASK_CLASSIFY_THINKING_TOKENS`, `ASK_WORDING_THINKING_TOKENS` | none | none; `app_env` sets it | image default: 1,024 thinking tokens for the planner and 512 for the others, never more than half the call's budget, sent where the model takes a thinking budget (Gemini 2.5 and 3; Gemini 4 Argon and later take a level alone). On Gemini 3 and later the planner is sent the level `low` instead when no thinking setting applies to it, since the pilot's measurement (#571) gave as many correct figures as any setting tried, fewer wrong ones than the 1,024-token default and the shortest tail at that level; a token setting that applies wins. A value must be a whole number of 128 or more; one the tier's model cannot honour stops startup, naming the setting. A Gemini Flash-Lite model is sent at least 512, its smallest budget |
| `ASK_PLAN_THINKING_LEVEL`, `ASK_CLASSIFY_THINKING_LEVEL`, `ASK_WORDING_THINKING_LEVEL` | none | none; `app_env` sets it | unset (on Gemini 3 and later the planner is sent `low` when no thinking setting applies); `low`, `medium` or `high`, sent in place of a thinking budget where the model takes a level ([The model](#the-model)); any other value, or one the tier's model cannot honour, stops startup, naming the setting |
| `MODEL_THINKING_TOKENS`, `MODEL_HIGH_THINKING_TOKENS`, `MODEL_MEDIUM_THINKING_TOKENS`, `MODEL_LOW_THINKING_TOKENS` | none | none; `app_env` sets it | unset; the thinking tokens of Ask's calls on every tier, or on one tier, under the call's own setting; a tier's wins over the global one |
| `MODEL_THINKING_LEVEL`, `MODEL_HIGH_THINKING_LEVEL`, `MODEL_MEDIUM_THINKING_LEVEL`, `MODEL_LOW_THINKING_LEVEL` | none | none; `app_env` sets it | unset; the thinking level of Ask's calls on every tier, or on one tier, under the call's own setting: `low`, `medium` or `high` |
| `ASK_UNKNOWN_WORD` | none: Compose names no model, so it has no effect | none; `app_env` sets it | image default `closest`: the planner reads a word the vocabulary does not list as its closest declared meaning, and says so; `abstain`: it abstains, and the answer's reason names the word. It applies to the planner under `ASK_ORDER=rules_first` only; under `model_first` the planner's own instructions apply. Any other value stops startup, naming the setting |
| `ASK_MODEL_CORRECTIONS` | none: Compose names no model, so it has no effect | none; `app_env` sets it | image default `2`: how many corrections Ask sends a model whose reply failed its contract or its checks, so at most three calls; `0` or `1` sends fewer. Any other value stops startup, naming the setting |
| `ASK_ORDER` | none: Compose names no model, so it has no effect | none; `app_env` sets it | image default `rules_first`: Ask answers on the release's rules first, certified, with no model call, and turns to the model only where the rules cannot answer; `model_first` has the model plan first, as 0.12 did. Any other value stops startup, naming the setting |
| `ASK_AI_FALLBACK` | none: Compose names no model, so it is off | none: image default `on`; `app_env` sets it | `on` or `off`. `on`: a question the rules cannot answer gets the model's plan, labelled AI-Generated; the SQL gate, the person's rights and the plan check still apply. `off`: certified answers only, worded by the template with no model call, and the rest refused. With no model for Ask (`MODEL_PROVIDER=none`, or no model on Ask's tier) it is off whatever it says. It is the deployment's alone: no request can change it. `GET /health` reports the value in effect. Any other value stops startup, naming the setting |
| `EVAL_CONCURRENCY` | none | none | image default: 8 questions at once in the job's margin check, A/B runs and test drive; a positive whole number overrides it, `1` asks one at a time. Results are the same at any value; lower it if Vertex AI or BigQuery quotas are tight |
| `RUN_REGION` | none | `region` | the Cloud Run location used to start the client's job; separate from the model location |
| `BIGQUERY_PROJECT`, `BIGQUERY_DATASET_PREFIX` | none | the project, and `kge` | where the client's BigQuery datasets are: `<prefix>_<client>_warehouses` and `<prefix>_<client>_graph`, and on the pilot `<prefix>_<client>_candidates`, with any `-` in the client's id written `_` |
| `SESSION_STATE_BACKEND` | `store` | `store` | Signed-out sessions and personal tokens use one JSON object, `access/<client>/identity/session-state.json`, in `STORE_URL`, independently of app state. Optional `bigquery` reuses the existing identity tables; switching does not migrate credentials and requires session key rotation ([Shared sign-in](#shared-sign-in-at-510-api-instances)). Compose uses the default and does not pass this setting from `.env`. |
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
| `PORTAL_CONTEXT_ASK_TITLE` | `Ask a governed question` | `portal_context_ask_title`, default as shown | The Context Ask header's title, returned by `/api/context/bootstrap` as `ask.title`. Trimmed; unset or blank uses the default; read when the portal starts |
| `PORTAL_CONTEXT_ASK_SUBTITLE` | `Certified definitions and figures, answered only from data you may read.` | `portal_context_ask_subtitle`, default as shown | The Context Ask header's subtitle, returned by `/api/context/bootstrap` as `ask.subtitle`. Trimmed; unset or blank uses the default; read when the portal starts |
| `PORTAL_LOGO_TITLE` | `off` | `portal_logo_title`, default `off` | `on` shows the product name beside a configured logo; `off` hides it. Without a logo the name remains visible. The home link retains an accessible name either way. Any other value stops portal startup; read when the portal starts |
| `PORTAL_KNOWLEDGE_HOME` | `on` | `portal_knowledge_home`, default `on` | The ALTA home, home link and app switcher; `on` or `off`, read when the portal starts |
| `PORTAL_APP_CERTIFY` | `on` | `portal_app_certify`, default `on` | The Certify stub, linking out to the Power App; `on` or `off`, read when the portal starts |
| `PORTAL_APP_CONTEXT` | `on` | `portal_app_context`, default `on` | The Context workspace inside the portal, using KGE through the portal; `on` or `off`, read when the portal starts |
| `PORTAL_APP_DISCOVER` | `on` | `portal_app_discover`, default `on` | Discover: browsing, search, questions and access requests; `on` or `off`, read when the portal starts |
| `PORTAL_APP_CEREBRO` | `on` | `portal_app_cerebro`, default `on` | Cerebro: chat with the agent, the graph you may read, and stewards' curation, using KGE through the portal; `on` or `off`, read when the portal starts |
| `PORTAL_CEREBRO_NAME` | `Cerebro` | `portal_cerebro_name`, default `Cerebro` | Cerebro's display name on its door, in the switcher and on its pages; trimmed, unset or blank uses `Cerebro`; set `Graph` to keep the earlier default; read when the portal starts |
| `PORTAL_TABLE_QUERY` | `off` | `portal_table_query`, default `off` | The Run Query tab on table pages and its server endpoint; `on` or `off`, read when the portal starts. `off` hides the tab and refuses queries; `on` enables them when KGE data reads are available. Any other value stops startup, naming the setting |
| `PORTAL_TABLE_PREVIEW` | `off` | `portal_table_preview`, default `off` | The Preview tab on table pages, its server endpoints and CSV exports; `on` or `off`, read when the portal starts. `off` hides the tab and refuses previews; `on` enables them when KGE data reads are available. Any other value stops startup, naming the setting |
| `PORTAL_DQ_FILTERS` | `on` | `portal_dq_filters`, default `on` | The four Data quality filter buttons; `on` opens on All, `off` hides the buttons and shows All. All lists named rules before rows without a rule, keeping each group's catalogue order. Any value other than `on` or `off` stops startup, naming the setting |
| `PORTAL_ASK_PROVIDER` | `kge` with a KGE client; `portal` alone | `portal_ask_provider`, default `kge` | Start of the `kge` → `portal` → `none` chain: KGE, the portal catalogue agent, then catalogue rules. `none` keeps the existing owner and fields answers without a model. An invalid value stops startup, naming the setting |
| `PORTAL_ASK_MODEL` | unset | `portal_ask_model`, default empty | The portal catalogue agent's model id. Unset or empty, the portal layer passes straight to catalogue rules without a model call. The agent reads only the person's readable catalogue within the page scope; no data or figures. Cerebro's chat (`POST /api/cerebro/stream`) uses the same model; unset, the chat answers "not set up". Both run on Gemini 2.5, Gemini 3.1 Pro (`gemini-3.1-pro-preview`) and Gemini 4 Argon (`gemini-4-argon`); on Gemini 3 and later the chat sends each tool call's thought signature back with it, and no thought text reaches the page |
| `PORTAL_ASK_MODEL_PROVIDER` | `vertex` | `portal_ask_model_provider`, default `vertex` | `vertex` or `mock` (offline tests), used only with `PORTAL_ASK_MODEL`. An invalid value stops startup, naming the setting |
| `PORTAL_CERTIFY_URL` | empty | `portal_certify_url`, default `null` (not passed); empty also omitted | Certify's external `http` or `https` address; unset or empty shows `[Power App link]` in its door and the switcher |
| `PORTAL_BACKEND`, `PORTAL_SQLITE_PATH` | `sqlite`, `/data/portal.db` | `bigquery`, unset | the portal's repository adapter and, locally, its SQLite database; BigQuery uses `_portal_*` tables in the client's existing warehouse dataset |
| `PORTAL_ASK` | `on` | unset: `on`; the portal module does not pass it | `on` or `off`. `off` clears the legacy `kge_url` field in `/brand`; `on` supplies `KGE_PUBLIC_URL` or `KGE_URL` where KGE is configured. GlobalBar does not read that field, so this setting has no effect on navigation. Ask buttons, panels and routes (`/ask`, `/api/ask`, `/ask-data-product`, `/ask-domain`, `/feedback-add`) stay available. To hide an app, use `PORTAL_APP_CONTEXT`, `PORTAL_APP_CERTIFY`, `PORTAL_APP_DISCOVER` or `PORTAL_APP_CEREBRO` instead. Any other value stops startup, naming the setting |
| `PORTAL_ASK_GUIDE` | `on` | `portal_ask_guide`, default `on` | Discover's Ask: shows (`on`) or hides (`off`) its **Guidelines** section; Ask's questions and answers are unchanged. Read when the portal starts; any other value stops the portal at start, naming the setting |
| `PORTAL_SEARCH_PROVIDER` | `kge` with a KGE client; `portal` alone | same | `portal` searches its own catalogue tables; `none` remains a legacy alias for `portal`. An unreachable KGE falls back with a page notice; existing verified sessions keep their units for at most five minutes |
| `PORTAL_DATA_PROVIDER` | `kge` with a KGE client; `none` alone | same | preview, export and query require KGE |
| `PORTAL_KGE_TIMEOUT` | 30 seconds | 30 seconds | portal container environment: timeout for KGE identity, session, catalogue and data calls, including the response body; whole seconds from 1 to 299 |
| `PORTAL_SEARCH_TIMEOUT` | 30 seconds | 30 seconds | portal container environment: timeout for KGE Search, including the response body; whole seconds from 1 to 299 |
| `PORTAL_ASK_TIMEOUT` | 285 seconds | 285 seconds | portal container environment: timeout for KGE Ask, including the response body; whole seconds from 1 to 299. Keep above KGE's model-call total (`MODEL_TOTAL_TIMEOUT`, 270 seconds by default) and below Cloud Run's 300-second limit |
| `PORTAL_CATALOGUE_FILE` | empty; `/catalogue/catalogue.yaml` for standalone | unset | the catalogue file when `KGE_URL` is empty; its host file is selected by `PORTAL_CATALOGUE_FILE_HOST` |
| `PORTAL_IDENTITIES_FILE`, `PORTAL_PASSWORDS_FILE` | empty; `/identity/principals.json` and `/identity/passwords.json` for standalone | unset | administrator-managed people and readable units, and KGE-format salted scrypt hashes; required without KGE |
| `PORTAL_SESSION_SECRET` | from the environment or `.env` for standalone | unset | at least 32 random characters; required without KGE; independent of KGE's session secret |
| `PORTAL_COOKIE_SECURE` | `false` for local HTTP | unset: `true` | Secure cookies for standalone sessions; turn on when serving HTTPS |
| `PORTAL_LOGO` | unset: the portal's built-in logo, in its header and as its tab icon | as Compose | the path in the portal's container of an SVG, PNG, JPG, JPEG or ICO logo image of 256 KB or less (a plain-drawing SVG: no doctype, script, event handler, animation, style sheet, embedded image, character reference or external link; the portal refuses to start on any other, naming the setting), served at `/brand/logo`, `/brand/logo.png` and `/brand/logo.svg` and shown in its header, tab icon and app manifest; on Compose, mount the file into the portal service and set this path, and on Cloud Run add a volume holding the file to the portal's service yourself, outside Terraform (the kit's Terraform mounts no logo), and set this path. It is read once as the portal starts, so a new file takes effect on restart (on Cloud Run, a new revision). Set one of `PORTAL_LOGO` and `KGE_LOGO`; when joined, KGE takes the portal's. The bar is navy in light and dark mode alike, so one version suffices: a light mark on a transparent background |
| `CLIENT_NAME` | `Client`, supplied from `PORTAL_CLIENT_NAME` | `portal_client_name`, default `Client` | KGE’s client display name for `<client> <product>` (default `Client ALTA`) in role guides and OpenAPI title; trimmed, unset or blank uses `Client`. Set the portal display name once for both services; independent of the profile id and pack title |
| `CONTEXT_APP_NAME` | `Context` | set through `app_env` | Context app name on the developer landing page; trimmed, unset or blank uses `Context`. Independent of Discovery’s `PRODUCT_NAME`. |
| `RUN_JOB` | the service’s `K_SERVICE` name | `job_names`, optional map of client id to job name in the pilot; omitted clients keep their service name | Job started by onboarding. Terraform sets `RUN_JOB` on the service only when the job name differs from the service name; its invocation grant follows that job. Compose leaves it unset for local jobs. |
| `PRODUCT_NAME` | `ALTA`, supplied from `PORTAL_PRODUCT_NAME` | `portal_product_name`, default `ALTA` | KGE’s product short name in `<client> <product>` (default `Client ALTA`) for role guides and OpenAPI title; trimmed, unset or blank uses `ALTA` |
| `KGE_LOGO` | unset | unset | the page's logo, the same contract as `PORTAL_LOGO`: the path in KGE's container of the image, checked as KGE starts (a bad one stops it, naming the setting) and served at `/brand/logo` from memory, so a new file takes effect on restart, with no rebuild; on Compose, mount the file into the app service, and on Cloud Run add a volume holding it to the service yourself, outside Terraform (the kit's Terraform mounts no logo). Unset with `PORTAL_URL` set, KGE asks the portal for its logo as it starts and keeps it, taking it only when the portal was given one; neither, the page shows the client's name beside its initial. Set one of them; when joined, KGE takes the portal's |
| `PORTAL_URL` | the portal's published address, `http://127.0.0.1:<PORTAL_PORT>`; KGE's start-up call for the portal's logo cannot reach that address from inside its container, so the page shows the client's name | set through `app_env`: the portal's `https` address ([Passwords and the service](#passwords-and-the-service)) | the portal's address as a browser reaches it, `http` or `https`: the developer page and role guides link to it, and KGE asks it for its logo as it starts |
| `KGE_PUBLIC_URL` | KGE's published address, `http://127.0.0.1:<KGE_PORT>` | unset: `KGE_URL`, which is public on Cloud Run | portal container environment: set to the address people reach KGE at. When `KGE_URL` is set, `/api/config` builds `contextMcpUrl` by appending `/mcp` to `KGE_PUBLIC_URL` (or `KGE_URL` if unset or empty), after removing trailing slashes. This is the MCP address shown in Context's MCP help. When `PORTAL_APP_CONTEXT` is on, a non-empty `KGE_URL` marks Context set up even if `KGE_PUBLIC_URL` is empty; `KGE_PUBLIC_URL` alone does not set up Context. On Compose, leaving it unset or empty gives `http://app:8000/mcp`, which a browser or agent outside Compose cannot reach. With `KGE_URL` empty or `PORTAL_APP_CONTEXT=off`, `contextMcpUrl` is null; `PORTAL_ASK=off` does not affect it. The address also supplies `/brand`'s legacy `kge_url` field, which GlobalBar ignores and which is null when `KGE_URL` is empty or `PORTAL_ASK=off`. Each address must be `http` or `https` with a host and no query: anything else stops the portal, naming the setting |
| `KGE_URL` | `http://app:8000` | the client's `kge-<client>` service URI | KGE's server-to-server address for session, identity, readable units and questions |
| `KGE_CALLER_AUDIENCE` | none | the page's own address, as `TRUSTED_CALLER_AUDIENCE` | on Cloud Run, the audience of the Google identity token the portal sends KGE as its caller token, `X-Caller-Token`, with each sign-in beside the browser's address; unset, none is sent |
| `PORTAL_SYNC_TOKEN` | none | Compose only; not set by the pilot | off Cloud Run, the shared secret sent only from the portal to KGE for whole-catalogue sync; the pilot uses the verified Google identity token instead |
| `SEARCH_PASSAGE_MIN_SIMILARITY` | `0.2` | `0.2` | search discovery floor on raw passage similarity, 0–1; zero-score hits always excluded |
| `SUGGESTED_MENTION_MIN_SIMILARITY` | `0.55` (hash) | `0.70` (real models) | Optional passage-to-term suggestion threshold in `[0, 1]`; blank defaults to `0.55` for hash embeddings and `0.70` for real models. Suggestions need steward acceptance to create links. |
| `SUGGESTED_MENTION_CAP` | `20` | `20` | Maximum suggested passage-to-term links per onboarding, highest similarity first; default `20`, nonnegative integer (`0` disables). |
| `PAST_QUESTIONS_RETENTION_DAYS` | `30` | `30` | Retention window in days for Context Ask's per-person past questions and conversations (`30` when unset; `0` disables retention). |
| `DATA_EXPORT_ROW_CAP` | `10000` | `10000` | maximum rows in one governed product CSV export; a positive whole number |
| `KGE_AUDIENCE` | none | the client's `kge-<client>` service URI | on Cloud Run, the audience of the Google identity token the portal sends KGE in `X-Serverless-Authorization`; unset, `KGE_URL`'s address. Off Cloud Run no token is sent |
| `TRACING` | the page and the portal: `.env`'s, `off` by default | each client's service and portal: `cloudtrace` with `tracing = true`, `off` otherwise; the job: none | traces, read by the page and the portal alike, each for its own spans: `off` (unset) exports nothing; `console` writes each span to the container's standard error; `otlp` sends spans over OTLP/HTTP to `TRACING_ENDPOINT`; `cloudtrace` sends them to Cloud Trace as the service account, on Cloud Run. Any other value stops the service, naming the setting. The portal starts a question's trace and passes it to the page, so one question is one trace. Spans carry names, ids, timings, counts, sizes and outcomes only, never a question's text, SQL, row values, document text, people's names or emails, or a model's prompt or reply |
| `TRACING_ENDPOINT` | the page and the portal: `.env`'s, `http://localhost:4318/v1/traces` when empty | none | the OTLP/HTTP traces address `otlp` sends to; inside a container `localhost` is the container itself, so name a collector the containers can reach |
| `TRACE_PROPAGATE_TO` | the portal: `.env`'s, else the portal's `KGE_URL` (`http://app:8000`); the page: none | each client's portal: its KGE service's address; the service and the job: none | the origins an outgoing call carries a trace context to: comma-separated `http` or `https` addresses (a path is ignored). Empty or unset, the image's default: none, so no call carries one. A listed origin gets the `traceparent` header alone, never `tracestate` or `baggage`, and only while tracing is on; any other destination gets nothing. Model providers, BigQuery, Secret Manager, Cloud Storage and every other third party are never on it by default: adding one is an explicit setting change. Anything but a list of addresses stops the service, naming the setting. The portal's default lets one question be one trace across the portal and KGE |
| `EGRESS_ALLOWED` | none | each client's service and job: the Google APIs the egress network reaches, from `egress_allowed`; the portal: none | the Google APIs this process's outbound traffic is limited to, comma-separated host names, which the test drive reports under *Telemetry and model posture*. The image reads it to report it and enforces nothing itself: the network Terraform puts the service, the job and the portal on does ([Network egress](#network-egress)). Unset, the report says no limit was given |

### Telemetry and trace context

Third-party telemetry and auto-instrumentation are disabled by default, with no exceptions: no library may send telemetry, usage pings, crash reports or injected trace/correlation headers to any third party; every SDK's own telemetry or auto-instrumentation is turned off explicitly at start-up in KGE and the portal; our own tracing (OpenTelemetry, off by default, opt-in via `TRACING`) is the only tracing, it only exports what its span processor allows, and trace context is sent only to the destinations `TRACE_PROPAGATE_TO` lists.

`docs/POSTURE.md` states what the KGE image does with no setting given: the model provider per tier (`MODEL_PROVIDER` and each `MODEL_<TIER>_PROVIDER`), `TRACING`, `TRACE_PROPAGATE_TO`, every SDK telemetry switch the image turns off at start-up, OpenTelemetry's propagator, and FastAPI's and the Anthropic SDK's own switches, which the code sets off. The kit's build reads each value from the image itself and names beside it the tests that prove it; nobody writes it by hand. A deployment changes a default only through the settings above.

The test drive attests an install the same way. Its report, printed in the job's log and saved under `runs/<client>/test-drive/`, has a section *Telemetry and model posture* with the same rows as the job reads them: the model provider per tier, the tracing mode, the propagation list, each telemetry switch's state and the Google APIs its egress is limited to (`EGRESS_ALLOWED`). It holds values alone, never a key, token or secret. The job is not traced, so its tracing mode and propagation list are the job's own; the service and the portal read their own `TRACING` and `TRACE_PROPAGATE_TO`, as the table above gives them.

### Network egress

On the pilot, each client's service, job and portal reach only the Google APIs they use, and each other; every other outbound address is refused. Terraform puts all three on one VPC network, `kge-egress`, through Cloud Run's direct VPC egress with all traffic routed through it, and that network leads nowhere else:

| Layer | What it holds |
|---|---|
| Routes | no default route to the internet, no Cloud NAT and no external address; the one route out leads to `private.googleapis.com` (`199.36.153.8/30`), which Private Google Access serves on the subnet |
| Firewall | `kge-egress-allow-google-apis` allows HTTPS (TCP 443) to `199.36.153.8/30`; `kge-egress-deny-all`, below it, denies every other destination and is logged, so a refused call shows in Cloud Logging as a firewall entry |
| DNS | private zones for `googleapis.com` and `run.app`, seen by this network alone: only the APIs `egress_allowed` lists, and each client's KGE and portal, resolve, each to `private.googleapis.com`; any other name in those domains does not resolve, and a name elsewhere resolves to an address the firewall refuses |

`egress_allowed` is the one list, by default `aiplatform.googleapis.com` (Vertex AI: the models, the embeddings and the document reader), `bigquery.googleapis.com`, `storage.googleapis.com`, `secretmanager.googleapis.com` (the passwords' live reload), `run.googleapis.com` (the page starts its job) and `www.googleapis.com` (Google's keys, which KGE checks the portal's identity token against). Terraform adds `<model_location>-aiplatform.googleapis.com` where `model_location` is a region, `telemetry.googleapis.com` (Cloud Trace) while `tracing = true`, and `dialogflow.googleapis.com` plus `<region>-dialogflow.googleapis.com` while `agent_studio = true`. Adding a destination is an explicit change to the list and an apply: an entry is one host name under `googleapis.com`, never a wildcard or another domain. A model provider, tier or embedding region that `app_env` points elsewhere fails until its host is listed. A warehouse runtime with an `execution_identity` also needs `iamcredentials.googleapis.com` in `egress_allowed`, where KGE asks for that identity's token. The runtime's tokens and identity come from Cloud Run's metadata server, which the network does not carry.

The limit: the firewall cannot tell one Google API from another, since they share `private.googleapis.com`'s addresses, so the per-name list holds by DNS. A process that sends a request to those addresses under an unlisted Google API's name still reaches that API, with its own account's rights alone. Only VPC Service Controls, which needs a Google Cloud organisation, limits which APIs and projects are reached at Google's end.

Enabling the Compute Engine API, which the network needs, makes the project's `default` network with its usual firewall rules on a project that has none; the pilot puts nothing on it and leaves it as it is.

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
| `vertex-gemini` | required: a Gemini model as Vertex AI names it, such as `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-3.1-pro-preview` or `gemini-4-argon` (Gemini 4 Argon) | works with the project's default quota |
| `vertex-claude` | `claude-haiku-5-5` unless set | Claude is a third-party model, enabled in Model Garden, and its quota on a new project is 0: every call fails with 429 (quota exceeded for the Claude base model) until a quota increase is granted, in the console's Quotas page |

`MODEL_HIGH_PROVIDER` and `MODEL_HIGH_NAME`, and the corresponding `MEDIUM` and `LOW` settings, define optional tiers in `app_env`; a tier with no provider falls back to the default provider and name together, except for the planner and Assist fallback below. Onboarding, enhancement, the Context Ask planner and Assist prefer high; answers and the document reader prefer medium; tagging prefers low. If high is unset, the planner and Assist try medium, then the default model, never automatically low. Keep the default model at medium or better for these complex jobs. `ONBOARD_MODEL_TIER`, `ENHANCE_MODEL_TIER`, `ANSWER_MODEL_TIER`, `CONTEXT_MODEL_TIER`, `TAGGER_MODEL_TIER`, `ASSIST_MODEL_TIER` and `READER_MODEL_TIER` can instead select `high`, `medium`, `low` or `default`. Add `MODEL_<TIER>_PRICE_IN` and `MODEL_<TIER>_PRICE_OUT` where the A/B report should cost a tier; a run pins its answer model and prices. A tier names its model in `MODEL_<TIER>_NAME` (only `vertex-claude` has a default), and calls its provider with its own `MODEL_<TIER>_API_KEY` and `MODEL_<TIER>_BASE_URL`. It uses `MODEL_API_KEY` and `MODEL_BASE_URL` only when its provider is the default's, so no provider is sent another's key or endpoint; the Vertex AI providers read no key at all. A selected tier with no provider follows the same fallback and logs why, and a price that does not parse is logged and left unknown. `MODEL_MAX_TOKENS` sets the reply budget of every model call, in place of the budget each step asks for (200 to 1,800 tokens), and `MODEL_HIGH_MAX_TOKENS`, `MODEL_MEDIUM_MAX_TOKENS` and `MODEL_LOW_MAX_TOKENS` set a tier's, which wins over it; unset, the image supplies 8192 tokens on every tier. Ask's three structured calls, below, are the exception: their own `ASK_<CALL>_MAX_TOKENS` wins over both, and with nothing set they take 16,384 or 4,096 tokens in place of 8192. The image's 8192-token default leaves room for thinking before the reply. Its attempt timeout is 180 seconds (`MODEL_TIMEOUT`), with one retry on transient errors. `MODEL_TOTAL_TIMEOUT` defaults to 270 seconds for both attempts together; each attempt is capped at the remaining time, and no retry starts after the deadline. The total must be positive and below the image’s 300-second model-call ceiling. `MODEL_REQUEST_TIMEOUT` defaults to 270 seconds for the whole answering request, including its model calls and graph and warehouse queries, in `/ask` (HTTP or MCP) and the reference agent. Its clock starts at service entry and includes time spent between calls. Each attempt receives the lesser of its own timeout, its call’s remaining total and the request’s remaining time; no call or retry starts with less than 0.05 seconds left. Exhaustion returns HTTP 503 with “The answer took too long. Please try a simpler question.”, or the same detail prefixed by `503: ` on MCP, never a partial answer. The request budget must be positive and below 300 seconds and ships in the image; an override is optional. The test-drive connectivity probe always requests 256 tokens, regardless of the configured budget. A value that is not a whole number above 0 is refused: the service does not start on one, and its log names the setting. `ONBOARD_MODEL_RETRIES` (0 to 5, default 2) is how often onboarding's drafting steps send a refused model reply back to the model before they fall back to rules, or, with a BigQuery backend on, stop. Model budgets and the timeout are defaults in the image, so a kit upgrade can change them. An operator may override them through `app_env`. Existing pilots need a one-time apply of this kit’s Terraform to remove the old budget variables from the service and job; any explicit `app_env` override still wins.

Ask's three model calls are structured: the planner (`plan`), the glossary classifier (`classify`) and the answer's wording (`wording`). Each sends a system instruction, then the data and the question last, and its contract's JSON schema as the provider's structured output (Gemini's `responseJsonSchema`); the reply is checked against the contract in code. A vocabulary or reading id is a plain string in every schema sent: the ids are in the data the model reads, and the checks and corrections enforce them. The full plan (`ASK_ORDER=model_first`) on every provider, and the planner on Gemini 3 and later (Gemini 4 Argon included), are the exceptions: they are always sent in JSON mode, with no schema as structured output and the schema in the system instruction instead. A call whose schema the provider refuses (an HTTP 400 naming the schema or the input token count, or, on a request that sent a schema, Vertex AI's generic "Request contains an invalid argument") is made again once in JSON mode, checked against the same contract, and that call's later corrections are sent in JSON mode too, so a refused schema is never sent twice in one question; the run trace records each call's `schema` (`sent`, `json` or `refused`) and marks the retry `schema_fallback`. The planner's contract holds its decisions alone, the vocabulary ids it chose and how it read the question's words, and the code fills in the rest. Each call has its own reply budget: `ASK_<CALL>_MAX_TOKENS` (`ASK_PLAN_MAX_TOKENS`, `ASK_CLASSIFY_MAX_TOKENS`, `ASK_WORDING_MAX_TOKENS`), else the tier's `MODEL_<TIER>_MAX_TOKENS`, else `MODEL_MAX_TOKENS`, else 16,384 tokens for the planner and 4,096 for the others. Thinking is capped within that budget, at 1,024 tokens for the planner and 512 for the others, and never at more than half the budget. On Gemini 3 and later the planner is sent the level `low` instead when no thinking setting (the call's, the tier's or the global one) applies to it, since the pilot's measurement (#571) gave as many correct figures as any setting tried, fewer wrong ones than the 1,024-token cap and the shortest tail at that level; the glossary classifier and the wording keep their defaults. At level `low` a planner reply cut off at its budget is not asked again with less thinking, as on Gemini 4; set `ASK_PLAN_THINKING_TOKENS` to `1024` for the earlier behaviour. `ASK_<CALL>_THINKING_TOKENS`, else `MODEL_<TIER>_THINKING_TOKENS`, else `MODEL_THINKING_TOKENS` changes the cap. The cap is sent as a token budget where the model takes one (Gemini 2.5 and 3). Gemini 4 Argon (`gemini-4-argon`) and later Gemini models take no token budget, since Vertex AI is reported to refuse one for them (to be confirmed by the pilot's live check on `gemini-4-argon`): a level that applies to Ask's calls is sent as set, and any token setting is then ignored; a token setting that applies with no level set (the call's, the tier's or `MODEL_THINKING_TOKENS`) stops startup and names it; with neither set, Ask's calls are sent the level `low`, in place of the token cap, so the glossary classifier's and the wording's 4,096-token budgets keep room for their reply; set a level to change it. A level (`low`, `medium` or `high`) set in `ASK_<CALL>_THINKING_LEVEL`, `MODEL_<TIER>_THINKING_LEVEL` or `MODEL_THINKING_LEVEL` is sent instead where the model takes a level and the level is set at least as specifically as any token setting; a level is the only thinking setting Claude and OpenAI models take. The two are never sent together. With a level set, the model decides how much it thinks within the reply budget: the half-budget cap applies to a token cap only. A setting the model cannot honour, such as a token budget on a Claude tier or a level on Ollama, stops startup and names the setting, as does, where a token cap is sent, a budget below twice the model's smallest thinking budget (256 tokens, or 1,024 on a Gemini Flash-Lite model, whose thinking is never sent below 512). A contract that nests (a filter of filters) is sent unrolled, since Claude takes no recursive schema and Gemini a cyclic one only outside a required field: three levels deep, and one on Gemini, as Vertex AI inlines every reference in the schema; the reply is still checked against the whole contract. On Claude (`anthropic`, `vertex-claude`) the calls use structured outputs (`output_config.format`), which Anthropic lists for Claude Opus 4.8 and later, Claude Sonnet 5 and 5.5, Claude Haiku 4.5 and 5.5 and the Fable and Mythos models (and the legacy Claude Opus 4.1 and 4.5); a Claude model without them refuses Ask's calls, so put such a model on another tier. `vertex-claude`'s default model, `claude-haiku-5-5`, has them. This Claude path is not yet proven against a live model: the tests check only the request it sends, and the live schema probe calls Gemini alone. On OpenAI and Ollama a reply budget is sent only when one is set, as before, and a reply cut off at the model's own limit says so. No temperature is sent to a Gemini 3 or later model, Gemini 4 Argon among them, on these three calls, as Google advises for Gemini 3. Gemini 3's other calls, the document reader, onboarding, enhancement, Assist and tagging among them, keep temperature 0, so their model identity and cache hashes do not move.

A reply that fails its contract, or a plan the checks refuse for a reason the model can fix (a plan check, a compile error, the SQL gate's static check or its dry run), is answered with a correction that continues the conversation: the model's previous reply, then the errors, each with the allowed choices where there are any. `ASK_MODEL_CORRECTIONS` (`0` to `2`, default `2`) sets how many, so a question takes at most three planner calls. A reply cut off at its budget is retried only when thinking less leaves the contract more room. Some failures are never retried: a refusal on access or restricted personal data, a question with no data for its period, a plan the cost gate or the partition check refuses, a failed call or outage, and the model's own abstention or clarification. The planner sends no correction when less than 30 seconds of the request remain.

Two corrections name what the model can change. A reading of a word as an id the model was not shown names the word and the id, and offers two readings: a declared id, or filler for a word that sets nothing; a word the model still leaves unread is a decline naming it, and the planner never reads a word as filler itself. A ranking asked in the plural ("rank the days of the week", "highest to lowest", "largest first") returns every group in rank order: a plan that limits it to fewer groups than the question states is sent back too. A stated count ("rank the top 3") keeps its limit, as does a question for the one group ("which day ranked highest"), and any other question keeps the model's limit. Where the model sees two declared meanings and the rules' best reading gives no figure, the answer is a clarification: Context shows each declared meaning as a button, and Cerebro's agent asks the person which they mean; picking one asks again with that meaning bound, checked as any other question.

`ASK_UNKNOWN_WORD` sets how the planner reads a word the vocabulary does not list: `closest` (the default) reads it as the closest declared meaning and records that reading; `abstain` abstains, and the answer's reason names the word. The planner's prompt version in the versions note changes with it. It applies under `ASK_ORDER=rules_first`; under `model_first` the planner keeps its own instructions.

When every planner attempt was cut off at its budget or failed its contract, the Trace tab's `model_plan_refused` step says so, naming the budget and the setting that raises it, before the rules' answer.

| Planning estimate | Figure | Source |
|---|---:|---|
| Output speed of the pilot's high tier (`gemini-3.1-pro-preview`) | about 125 tokens a second | #559's pilot measurement |
| A full 16,384-token planner reply | about 130 seconds | the speed above; inside `MODEL_TIMEOUT`'s 180 seconds |
| A typical planner reply with thinking capped at 1,024 tokens (a set token budget; Gemini 3 and later default to level `low`) | 1,000 to 3,000 tokens, about 10 to 30 seconds | not measured |

Do not raise the planner's budget past about 20,000 tokens without raising `MODEL_TIMEOUT` and the request timeouts with it: one full reply would no longer fit an attempt. Three full-length attempts do not fit the 270-second request budget either, which is why corrections stop near its end.

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
| `bigquery_create_reservation` | `false` | opt in to creating and assigning an Enterprise slot reservation to the execution project; adds capacity billing |
| `bigquery_max_slots` | `50` | autoscaling slot maximum when the reservation is enabled; other BigQuery charges remain separate |
| `model_location` | `global` | the Vertex AI location the model is called in |
| `session_key_version` | required (`"1"` in `example.tfvars`) | the explicit numeric version of every client's session-key secret; never `latest`; change it through a deployment as below |
| `min_instances` | `1` | the minimum warm Knowledge API instances; the installer chooses it; acceptance uses `5` |
| `requests_per_instance` | `80` | concurrent API requests per instance, whole number 1–1000; polling releases workers between requests |
| `tracing` | `false` | `true` sets `TRACING=cloudtrace` on each client's service and portal, which then export traces to Cloud Trace as the client's service account; `false` sets `TRACING=off` and nothing is exported. The job is not traced |
| `agent_studio` | `false` | `true` enables `dialogflow.googleapis.com` and `discoveryengine.googleapis.com`, adds `dialogflow.googleapis.com` and `<region>-dialogflow.googleapis.com` to the egress DNS, creates the Dialogflow service identity, and with `public = false` grants `roles/run.invoker` on each client's KGE service to the Vertex AI and Dialogflow service agents ([Cloud Run invokers for an external agent](#cloud-run-invokers-for-an-external-agent)) |
| `egress_allowed` | Vertex AI, BigQuery, Cloud Storage, Secret Manager, Cloud Run's API and `www.googleapis.com` | the Google APIs each client's service, job and portal may reach, each one host name under `googleapis.com`; with the regional Vertex AI host, Cloud Trace's while tracing is on, Dialogflow's while `agent_studio` is on, and each client's KGE and portal, nothing else is ([Network egress](#network-egress)) |
| `max_instances` | `10` | the maximum Knowledge API instances on the BigQuery pilot; keep file-backed deployments at one |
| `pilot_env` | the GCP-native configuration ([The model](#the-model)) | the pilot's settings for the service and the job: Gemini on Vertex AI by tier, Vertex AI embeddings, BigQuery for the warehouses, retrieval and app state; `{}` leaves the image on its local defaults |
| `app_env` | `{}` | more of the image's settings for the service and the job, each over `pilot_env`'s of the same name: the model's ([The model](#the-model)) and BigQuery's ([BigQuery](#bigquery)) |
| `public` | `true` | whether anyone may reach each client's page; `false` where the organisation refuses `allUsers` ([A private page](#bootstrap-the-project-once)) |
| `invokers` | `[]` | with `public = false`, who may reach each page: IAM members (`user:`, `group:`, `serviceAccount:`, `domain:`), or a `principal://` or `principalSet://` identity; never `allUsers` or `allAuthenticatedUsers` ([Cloud Run invokers for an external agent](#cloud-run-invokers-for-an-external-agent)) |
| `image` | `null` | the image the service and the job start with; `null` starts them on Google's sample until your first deployment. Terraform never moves it after |

**Traces.** With `tracing = true`, Cloud Trace shows each question as one trace, from the portal through the page's stages and model calls to its BigQuery jobs, with each span's timing and outcome; filter by the service, `kge` or `kge-portal`. The pilot enables the Cloud Trace and Telemetry APIs and grants each client's service and job accounts `roles/cloudtrace.agent` whatever the variable says, so turning tracing on or off is a `terraform apply` alone, which makes a new revision of each service. Spans carry no question, SQL, values, names, prompts or replies. Since Cloud Run gives an idle instance no CPU, each service sends a question's spans before that question's response completes, waiting at most 2 seconds; other requests' spans go with the next batch and never wait. When the trace endpoint is slow, a question can therefore wait up to 2 seconds in each service, about 4 seconds in all for a question asked in the portal. With tracing off, neither service starts a tracer or an exporter.

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
| `eval` | the A/B run on the question sets, or, with `SUITE=<file or gs:// URL>`, a custom or templated UAT question suite against the served graph and its warehouse on `ARM=<schema, catalog, graph, control, treatment or all>` (enforcing `BIGQUERY_MAX_BYTES_BILLED` when set) |
| `tagger-eval` | passage scoring for RuleTagger and all three tiers; `LIVE=yes` uses configured models and requires `TARGETS`, `AB=yes` adds the served graph’s answering A/B; saves `runs/<client>/tagger-eval/<run>/` ([Tagger evaluation on the pilot](#tagger-evaluation-on-the-pilot)) |
| `baseline` | records the released graph on rules as `baselines/<graph store>-<UTC time>.json` and updates the profile, keeping people and asker; also runs after every successful release |
| `teardown` | `CONFIRM=<client>` removes client data after a store backup; `PREVIEW=yes` lists it only; `PEOPLE=remove` also removes deployment identities and profile; no page or MCP control |

The page persists the short source, draft, acceptance and certification edits under its own `clients/<client>/units/` prefix. Those inputs stay in `clients/`; generated warehouses and graph state stay in the job's `work/`, and releases stay in `releases/`. On Google Cloud, Run checks, sign-off and release write a validated request and start the client's fixed `onboard-request` job without overrides. The page reads the job's store marker whenever the page polls. Under Compose there is no Cloud Run API: the page accepts the request and returns HTTP 202 with the exact `docker compose run --rm job onboard-request` command for the operator; the request is validated in the same way. One request may run for a client at a time. A `running` marker older than 185 minutes—beyond the job's three-hour timeout—becomes `stale` and may be superseded, so an unrun command or a terminated job does not block the page indefinitely. Compose and Cloud Run pages follow a completed page/API release on the next status poll: with `STORE_URL` set, each process fetches and switches to the store's current graph. The pilot has at most one instance. The helper's `publish` and `unpublish` use this path; `kge wait` polls it. A direct operator `release` command does not write that page job marker, so restart the service after it to ensure every process follows.

## Building the images on your own base

The kit's images are built on Red Hat's signed Universal Base Image 9 minimal bases from public package indexes: Python 3.12 for KGE and Node.js 22 for the portal, both on RHEL 9.8 and pinned by digest. A team that must build on its own base, from its own index and behind its own certificate authority, builds both images from their source folders, the KGE source folder and the portal source folder, with these build arguments. Tag them `kge:@VERSION@` and `kge-portal:@VERSION@` and the steps above use them in place of the kit's tarballs: skip those two `docker load` commands.

Neither image uses Kerberos: on a base with `rpm`, each image's final stage removes `krb5-libs` and the packages that need it (`libcurl-minimal`, `curl-minimal`, `librepo`, `cyrus-sasl-lib`, `openldap`, `gnupg2`, `gpgme`, `libtirpc` and `libnsl2`, those the base has), with the package tools, so no Kerberos library is left in either image.

In `kge:@VERSION@`, `/usr/share/doc/kge/LICENSE` holds the pilot licence and `/usr/share/doc/kge/THIRD_PARTY_NOTICES.md` the dependency notices, with the standards' notices in `/usr/share/doc/kge/STANDARDS_NOTICES.md`. In `kge-portal:@VERSION@`, the identical pilot licence and the portal's dependency notices are `/usr/share/doc/kge-portal/LICENSE` and `/usr/share/doc/kge-portal/THIRD_PARTY_NOTICES.md`. The kit also carries the notices as `notices/KGE_THIRD_PARTY_NOTICES.md` and `notices/PORTAL_THIRD_PARTY_NOTICES.md`.

| Image | Build argument | Default | What it sets |
|---|---|---|---|
| KGE | `BASE_IMAGE` | Red Hat's signed `registry.access.redhat.com/ubi9/python-312-minimal:9.8`, by digest | the base: any image with Python 3.12 as `python3`, and `sh` |
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

### Shared sign-in at 5–10 API instances

Before applying the kit's Terraform to an existing pilot, add a new random version of each client's `kge-<client>-session-key` secret privately using the bootstrap secret procedure, then pin `session_key_version` in its tfvars to the new enabled numeric version; do not assume the `"1"` in `example.tfvars` matches. Installations whose clients use different version numbers must align those numbers first. The shared sign-out list starts empty, so this rotation ensures every pre-upgrade cookie is refused; people sign in once again. Keep `min_instances=1` and `max_instances=1` during the upgrade, apply the kit's Terraform with the installation's existing variables and backend, then redeploy the shipped KGE image using the deployment steps below. Terraform does not move the image. Wait until the new revision serves all traffic and the previous revision receives none; do not split traffic between old and new revisions. Only then set `min_instances=5` and `max_instances=10` and apply again. The pilot default maximum is now ten; the minimum remains an installer variable.

On Cloud Run, `SIGNIN=passwords` refuses startup without `SESSION_SECRET`. Each startup logs `session key fingerprint=` followed by twelve hash characters, never the secret. Confirm the same fingerprint on every instance. Sign in on each instance and check 20 requests per API and UI route at 5–10 instances, including Context and Ask. Replay a cookie after signing it out on another instance: every instance must refuse it within thirty seconds (normally within three seconds; a stalled identity refresher returns 503). Check a personal bearer token on every instance too. These are acceptance checks, not a load or sizing test.

Signed-out session ids and personal tokens are shared through the session-state adapter, independently of `APP_STATE_BACKEND`. By default (`SESSION_STATE_BACKEND=store`), one private JSON object, `access/<client>/identity/session-state.json`, lives in the shared store: the pilot's GCS bucket or a folder locally. Conditional writes merge concurrent sign-outs and token creation. Access grants stay in their existing backend. Session state survives client-data teardown and is excluded from pack, work and release snapshots. Each process refreshes its snapshot in the background every three seconds; a new personal token normally takes that long to reach another instance. Requests normally read memory only; after an idle period leaves a snapshot at least thirty seconds old, concurrent requests share one synchronous store read, with zero BigQuery jobs on the default backend. A failed read returns 503 without accepting old credentials. Optional `SESSION_STATE_BACKEND=bigquery` reuses the existing protected identity tables and adds one BigQuery refresh job after idle; it is not the pilot default. Changing `SESSION_STATE_BACKEND` does not migrate tokens or sign-outs: before applying the change on the pilot, add a new random version of each session-key secret and pin `session_key_version` to it as described below, then move all traffic together. Without Terraform, replace `SESSION_SECRET` with a new random key before restarting the service with the changed backend. Once all traffic uses the new key, old cookies, including signed-out ones, are refused; people sign in once again. Existing local run-folder tokens remain local credentials; obtain a new pilot bearer with the password exchange in [Tokens for an agent](#tokens-for-an-agent). Browser sign-out does not revoke the separate fifteen-minute signed bearer.

For an existing pilot, apply this kit's Terraform before deploying the image: it adds `roles/storage.objectUser` for the service on its own `access/<client>/identity/session-state.json` object only. The job's store-wide grant is unchanged.

The password sign-in throttle is **per instance**, per client-address/username pair: ten attempts per minute; five failures in fifteen minutes lock that pair for five minutes on that instance. A successful sign-in clears that pair's counters; a restart clears all counters. At five instances, distribution can allow up to fifty attempts per minute or twenty-five failures before every instance locks; at ten, up to one hundred attempts or fifty failures. A lock on one instance does not lock another. Shared transactional throttling would add a BigQuery job to every sign-in attempt, so this kit keeps the existing per-instance limits. Apply an upstream rate limit if the installation needs a fleet-wide limit.

To rotate the key, add a new random secret version privately using the bootstrap secret procedure, record its numeric version in `session_key_version`, apply the kit's Terraform and redeploy the shipped KGE image. Move all traffic to that revision before resuming normal use or raising instances. Do not disable the old secret version until old instances receive no traffic. Rotation invalidates existing browser sessions and signed bearer tokens; people sign in again. Personal tokens stored in session state keep their current membership checks. Local and Compose runs retain their generated key when no key is supplied.

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

#### Egress limited to the Google APIs the pilot uses

The next `terraform apply` creates the `kge-egress` network, its route, firewall rules and private DNS zones, enables the Compute Engine and Cloud DNS APIs, and puts each client's service, job and portal on that network for all their outbound traffic, as [Network egress](#network-egress) describes; each service gets a new revision. Before applying, check that every Google API your `app_env` reaches is in `egress_allowed`, with the default list and the hosts Terraform adds. After it, run the test drive: its report's `EGRESS_ALLOWED` row gives the list in force, and a call to any other address is refused and logged as `kge-egress-deny-all`.

#### Certified client-directed NNA, source-system names and the Graph tab

Deploy both shipped images using the upgrade steps below; the portal image carries the new Graph tab. No setting or Terraform change is needed.

Meridian's example pack changed; its fixture did not. A breakdown by source system shows each system's name beside
its code. Certified and client-directed NNA follow the approved golden SQL: they read `F_NNA_FIN` by
`DW_TRAN_MNTH_END_DT`, without an `FRZN_IN` filter. Remove the earlier certified modifier and `finance_frozen`
filter when updating an older Meridian example pack. For an installation using the unchanged example, upload the
new kit's complete `examples/meridian/` pack and onboard as in
[Meridian's expanded NNA questions](#meridians-expanded-nna-questions) below, then review, sign off and release.
For a client's own pack, preserve the client's approved metric definitions and declare a code/name pair for any
coded dimension whose names people should see.

The reporting bases are declared once in `terminology.concepts`, with all the wordings for each mode:

```yaml
concepts:
  - id: reporting_basis
    time_mode: as_reported
    wordings: [as reported, as originally reported, certified, published, frozen]
    qualified_terms: [{noun: NNA, term: certified-nna}]
  - {id: current_basis, time_mode: restated, wordings: [current, current version, current versions, restated]}
```

In `terminology.code_name_pairs`, its branch division pair is:

A bare display name selects its declared code only when the member and readable code value are unique. An explicit
name-column selector keeps its literal name filter; ambiguous or unreadable mappings grant no code read.

```yaml
- {code: wealth.BRCH_DIM_1.BRCH_DIVSN_CD, name: wealth.BRCH_DIM_1.BRCH_DIVSN_NM, members: [{code: BR, name: Retail Branches}, {code: WEST, name: Central and West Division}]}
```

At engine open, the release calendar adds one query and its dry run per eligible runtime.

A BigQuery runtime whose `location` names a data project different from its `billing_project` now answers streamed questions in Context; it needs no change to its binding.

#### Meridian's expanded NNA questions

Deploy both shipped images using the upgrade steps below. Meridian's example fixture now has one more year of history; every earlier row is unchanged. Installing a new kit or changing the image does not change data already loaded. For an installation using the unchanged Meridian example, upload the new kit's complete `examples/meridian/` pack and fixture as [A client from its own fixture](#a-client-from-its-own-fixture) describes. Preserve any client-specific pack edits in your complete input folder first; do not replace a client's own data with the example.

Before onboarding, if your users have short names for a measure (for example a nickname for client-directed NNA), add each as a phrase on that term in your pack; otherwise questions using it abstain as an unknown word.

From the kit's `compose/` folder, with the helper already registered to the installed client:

```sh
kge upload ../examples/meridian
```

The helper runs `onboard FROM=` after uploading. On Google Cloud, select the installed client with `kge use gcp/<client>` first and give `kge upload` the new kit's example folder. `onboard` builds the new graph from scratch from the pack and landed data; it does not merge graph files. Review the candidate's input/schema changes and passed checks in Context before signing off. Do not run `lock` or `seed`.

The stewards' decisions saved with the job's graph store are kept in the runtime overlay: an append-only `decisions.jsonl` journal beside the graph, replayed onto every new graph. Check that journal contains the expected decisions before replacing an instance: decisions made only on a running page's temporary copy retain the lifecycle limits described under [Troubleshooting](#troubleshooting). After `onboard`, inspect its log for `overlay orphan` warnings and check `orphans.json` before sign-off. In kit installations these files are archive members `build/stores/<client>/overlay/decisions.jsonl` and `build/stores/<client>/overlay/orphans.json` in the store's `work/<client>.tar.gz`; these are generated job files, not files to add to the uploaded pack. Download the work archive on Google Cloud:

```sh
gcloud storage cp "$STORE/work/$CLIENT.tar.gz" "work.$CLIENT.after-onboard.tar.gz"
```

Or, from the installed kit's Compose folder:

```sh
curl -fsS -o "work.$CLIENT.after-onboard.tar.gz" "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/work%2F${CLIENT}.tar.gz?alt=media"
```

Then read the orphan report without extracting or changing the journal:

```sh
tar -xOf "work.$CLIENT.after-onboard.tar.gz" "build/stores/$CLIENT/overlay/orphans.json"
```

Each replay stage should have an empty list. None are expected for this example upgrade because earlier rows and documents are unchanged. Each orphan lists its tag, decision, person, chunk and reason. Resolve any unexpected orphan before sign-off:

| Reported reason | Action |
|---|---|
| Document or chunk missing | Check the uploaded fixture is complete; restore an accidentally omitted source and onboard again. If removal was intentional, have the steward review the retired decision. |
| Chunk text changed | Check the intended document version; the steward reviews the new text and decides its new proposal in Context. The old decision must not attach to different text. |
| Term missing or not replayed | Restore its intended declaration or source, then onboard again and have the steward review it. |
| Match no longer lands for review | Check whether the source certified or removed it upstream; have the steward confirm the upstream outcome or restore the missing source. |
| Chunk no longer cleared for embedding | Resolve its current governance/readiness review with the steward; do not carry the old mention decision across that restriction. |

Keep the journal and report for review; do not reset the overlay to hide an orphan. Once the changes and replay are reviewed, sign in as a steward to sign off, then as the knowledge engineer to release and restart:

```sh
kge login <steward-email>
kge sign-off
kge wait
kge login <knowledge-engineer-email>
kge release
kge restart
kge served
```

`kge restart` restarts Compose's app. On Google Cloud a direct operator release needs a new service revision: use the restart command in [A client from its own fixture](#a-client-from-its-own-fixture), then use `kge served` to verify the reviewed hash. The helper's Google Cloud `restart` itself does not create that revision.

Meridian's NNA definition now reads: “Money clients bring to the firm less money they take away, in USD, over released months: eligible flows only, with test accounts left out. A move between a client's own accounts is not new money.” NNA no longer consults the account exclusion list; the excluded-account count golden item is retired. The NNA, certified NNA, client-directed NNA and branch-sales NNA contexts and their flow-amount inputs now stay within nine Wealth views: `F_NNA_FIN`, `TRAN_FACT_1`, `TRAN_FACT_EXCL_MFCLR_G`, `MONTH_DIM_1`, `ACCT_DIM2_1`, `CLIENT_CD_ROLLUP_1`, `BRCH_DIM_1`, `SRCE_SYS_1` and `DATE_DIM_1`. They use the account's domicile branch and `LAST_RCV_HSHLD_DK`, branch region/division columns and client-code segment levels. Other products remain in the portal catalogue. Plain year to date starts in January; fiscal year to date uses the fiscal calendar, and answers state the window. Percent change divides by the absolute comparison value. Review the definition with the business owner, then upload the updated pack and fixture, onboard, sign off, release and restart as above; no new setting is required.

The planner and Assist now prefer the **high** model tier; answer phrasing remains medium and tagging remains low (the pilot's explicit tagger setting still wins). With the pilot's high tier configured and no `CONTEXT_MODEL_TIER` or `ASSIST_MODEL_TIER` override, the new image's defaults are enough: no new environment setting or Terraform change is needed for this tier choice. Remove an old explicit low override or select `high` (or `medium`) through the installation's environment settings. These two agents fall back from an unconfigured high tier to medium, then the default model, never automatically to low; keep the default model at medium or better. Compose still uses rules until a model is configured.

In Context, sign in as an enterprise analyst, select the graph arm with model planning on the pilot, and ask the questions below. They use only the kit's invented Meridian content. Check the shown source, filters and period as well as the figure; a privacy or clarification response must contain no figure. The rules-only Compose install can check data loading, but these model-plan shapes require a configured planner on the pilot.

| Capability | Check question | Check the answer |
|---|---|---|
| Added history and year comparison | What is NNA for April 2025 versus April 2024? | Both released periods have figures. |
| Versioned client-code joins and segment levels | NNA for segment level 4 WLTH, segment level 5 AFFLUENT and segment level 6 CORE during April 2025 | All three filters apply (codes S3200, S4100 and S5100); versions do not multiply the amount. |
| Branch filter | NNA for the Fort Collins branch during April 2025 | Only branch 5H contributes. |
| Region filter | NNA for region NORTHEAST during April 2025 | Only region CEO contributes. |
| Direction outputs and ratio | Flow amount with inflows and outflows and ratio of inflows to outflows during April 2025 | Separate direction amounts and the ratio use the declared flow-amount measure. |
| Certified month-end NNA | Certified NNA during April 2025 | The source is the frozen finance figure. |
| Branch-sales NNA | Branch sales NNA with outflows during April 2025 | The source excludes mutual-fund clearing and applies the received/released rules and outflow filter. |
| Household breakdown and count | Branch sales NNA by household with number of households during April 2025 | Household keys, distinct counts and the total appear. |
| Transaction count and amount threshold | Branch sales NNA with number of transactions with transaction amount at least 20000 during April 2025 | Only transactions meeting the threshold contribute to the amount and count. |
| Account relationship split and share | NNA by account relationship with share of total during April 2025 | Yes/no groups and their shares reconcile to the total. |
| Day-of-week join | NNA by day of week during April 2025 | Transaction dates supply the day groups. |
| Source-system join | NNA by source system name during April 2025 | Source names appear. |
| Year to date | NNA year to date through June 2025 | The answer shows the declared calendar's year start and includes June. |
| Both ranking ends | Top 10 and bottom 10 households by NNA during April 2025 | Both selections are labelled. |
| Most/least wording | Which weekday had the most certified NNA year to date through April 2025? | All seven weekdays are ordered from greatest to least. |
| Totals and subtotals | NNA by region and month from April 2025 through June 2025 with totals | Group rows, period subtotals and the overall total reconcile. |
| Difference and direction of percentage change | NNA by month from April 2025 through June 2025 with $ change and percent change from the previous period | Current minus previous, divided by the absolute previous value for percent; the first month's changes are blank. |
| Current versus certified population | NNA and Certified NNA and their difference during April 2025 | The declared comparable population is used. |
| Filtered follow-up | Now just with account relationship | Following the comparison above, both measures retain the relationship filter. |
| Ambiguous meaning | What is Retail NNA during April 2025? | Asks which Retail meaning. |
| Undeclared measure | What is total client assets during April 2025? | Offers declared measures and asks which one. |
| Unreleased month | What is NNA during July 2027? | Offers the latest released month. |
| Privacy | Show client names with NNA during April 2025 | Cites the data-classification policy and offers household keys. |

To roll back this content upgrade, run `kge release PREVIOUS=yes`, then restart the service as above and verify it with `kge served`. This restores the previous retained release, its declared inputs and its warehouse versions, including the previous fixture; live access grants stay live. Keep the previous shipped image digests too, so an image rollback can follow the steps below. A pruned older release cannot be recovered by selecting its hash.

#### Calendar declarations for period ranges

Declare each calendar's `year_start_month` and `quarter_label`, which contains `{n}`. Optional `spine` names the
complete day calendar, with `table: source.table` and `column: date_column`; the column must be `DATE` and the
table's single-column primary key. Finite requested ranges derive day, week, month, quarter and year spines from its days, including periods with
no fact rows. A month table that covers only some dates is not a complete day spine.
The spine applies to its declared source; another source sharing the calendar retains its existing period generation.

Optional `spine.attributes` maps attribute ids, such as `week_of_month`, `month_of_year` and
`year_quarter`, to fully qualified text or numeric columns in that source.
Fiscal year and quarter derive from the calendar's start month and label, without a
physical fiscal column. ISO week-year derives from the date rather than the calendar-year attribute.

Optional `spine.recurrences` declares recurring values with `attribute`, `grain`, `cycle` and
`values: [{value, phrases: [...]}]`. For example, a `month_of_year` attribute with `grain: month`, `cycle: year`
and `{value: 6, phrases: [June, Jun]}` supports “every June, 2023 to 2025”. A `week_of_month` attribute with
`grain: week`, `cycle: month` and `{value: 2, phrases: [week 2]}` supports “week 2 of each month”. The cycle is a
coarser time breakdown; each value matches the declared attribute column's type. Attribute ids, values and wordings
are declared once, and each context needs a declared path to the attribute table. Month-grain measures decline
recurring week filters. A named range still requires released observations, without silently omitting unavailable years.

Optional `current_period` is an explicit date, for example `"2025-07-15"` when the source states that the data is
booked through that day. A question's stated period takes precedence; without a stated period or declared
anchor Ask requests a period. It never uses the machine's current date. A context's `ytd_calendar` sets plain year
to date independently of the measure's calendar; explicitly fiscal year to date uses the fiscal calendar.
To-date cutoffs include the final day of the stated period; month and quarter to date start in the month or quarter
containing that day. A declared current period does not make an open month released.

Questions may use month ranges ("January through September 2025", "Jan-Sep 2025"), quarter ranges ("Q1 through
Q3 2025", "first three quarters of 2025") and year, month or quarter to date. A breakdown such as "by month"
remains within that range. Upload the changed pack and calendar data, run `kge check-pack`, then onboard, review,
sign off and release as above. No setting or Terraform change is required.

#### Filter groups and lists of values

Deploy the shipped KGE image using the upgrade steps below. No setting or Terraform change is needed. Onboard the
updated pack and compare its figures with the approved reference SQL before release.

Business reporting bases are declared once in `terminology.concepts`, with one target and all its wordings:

```yaml
concepts:
  - id: reporting_basis
    time_mode: as_reported
    wordings: [as reported, as originally reported, certified, published, frozen]
    qualified_terms: [{noun: NNA, term: certified-nna}]
  - {id: current_basis, time_mode: restated, wordings: [current, current version, current versions, restated]}
```

Optional `qualified_terms: [{noun, term}]` expands every `<wording> <noun>` into an alias for the declared glossary
term. The noun must be nonempty; generated phrases undergo the ordinary duplicate and target checks. New wordings
need only a pack change and onboarding. Compare prefix, separated-prefix and postfix templates.

Use `{phrase: <wording>, column_wording: source.table.column}` for a lookup wording that references an existing
measure column without renaming its displayed name. The compiler checks the target; the caller's column access
still applies. A `column` alias continues to supply a display name as well as a lookup name.

Each wording occurs once across concepts and aliases. Check-pack refuses repeated wordings and wordings assigned to
two concepts. Onboard a pack wording change through the ordinary candidate loop, then compare every wording's plan
and figures before sign-off. For "NNA for March 2025" followed by a reporting-basis wording ("as reported", "certified",
"published" or "frozen"), Meridian's sample reads Certified NNA and gives 5,929,714.83; a current-basis wording or no
explicit basis gives 9,479,714.83. Each wording selects the declared time mode of the named metric. Finance NNA reads
`F_NNA_FIN` by `DW_TRAN_MNTH_END_DT` without an `FRZN_IN` filter; "certified" and "frozen" do not add one.
Prefix names such as "published NNA" and "Certified NNA" read the same and give 5,929,714.83 for March 2025, as does
postfix "NNA … as reported". A comparison such as "as reported vs current" still reads both sources.

A pack filter phrase can now stand for several codes. In `terminology.predicates`, give `op: in` and the codes in `values`, with no `value`. A phrase excluding those codes uses `op: not_in`. Meridian's example pack declares both:

```yaml
- {phrases: [coastal region, coastal regions], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['CEO', 'NER']}
- {phrases: [outside coastal region], column: wealth.BRCH_DIM_1.RGN_CD, op: not_in, values: ['CEO', 'NER']}
```

With it, "NNA for Jan 2025 for coastal region" reads `RGN_CD IN ('CEO', 'NER')`. `values` needs two or more codes, all text or all numbers, each once. `kge check-pack` reports an empty or one-item list, mixed types, `value` given with `in` or `not_in`, and `values` given with any other operator.

Other filter phrases may use `eq`, `ne`, `gt`, `ge`, `lt`, `le`, `is_null`, `is_not_null` or `is_true`. `eq`, `ne`, `gt`, `ge`, `lt` and `le` take one text or numeric `value` compatible with the column. `is_null`, `is_not_null` and `is_true` take neither `value` nor `values`.

For text matching, declare `contains` or `starts_with` with one nonblank text `value` on a text column:

```yaml
- {phrases: [matching labels], column: ledger.events.label, op: contains, value: Al}
- {phrases: [initial labels], column: ledger.events.label, op: starts_with, value: Al}
```

The first phrase matches `Al` anywhere in the label; the second matches only at the start. Both are case sensitive,
and null labels do not match. `kge check-pack` refuses nontext columns, nontext or blank values and LIKE wildcards
`%` and `_`. SQL quoting preserves literal quotes and escape characters. The gate permits exactly the declared
operator, value and column.

A question can also name two or more values of one column, joined by "and", "or" or commas: "Northeast and Pacific regions", "Northeast or Pacific region" or "Northeast region and Pacific region". Each value needs its own `eq` phrase sharing the filter's noun, as `NORTHEAST region` and `PACIFIC region` do.

"Excluding", "except", "other than" or "not in" followed by one or more declared values excludes them; lists use the same joining words. That wording and a `not_in` phrase keep rows with no value: `NOT (RGN_CD IN (...)) OR RGN_CD IS NULL`. The answer says those rows are kept. Words inside a declared name belong to that name, so a measure named with "excluding" does not add an exclusion.

Values of different columns stay separate filters. "Or" between two declared filters keeps rows matching either filter, once; "and", or no joining word, requires all the filters. Mixed "and" and "or" without clear grouping asks the person to clarify the grouping.

To add a group on an installed client, add the predicate to the client's pack and upload it as in [A client from its own fixture](#a-client-from-its-own-fixture). Then onboard, review the candidate in Context, sign off and release.

#### Banded dimensions

Top-level `banded_dimensions` declares a numeric column's labelled ranges, for example:

```yaml
banded_dimensions:
  - id: amount_bands
    label: amount band
    column: ledger.events.amount
    ranges:
      - {label: Small, min: null, max: 1000}
      - {label: Medium, min: 1000, max: 10000}
      - {label: Large, min: 10000, max: null}
```

"Revenue by amount band" groups by those labels. Each range includes `min` and excludes `max`; null is an
unbounded endpoint. The first minimum and final maximum must be null. Other bounds are finite numbers, ordered
and contiguous. `kge check-pack` refuses gaps, overlaps, unsorted ranges, duplicate or blank labels and nonnumeric
or restricted columns. Null source values keep a null grouping label. The SQL gate refuses changed columns,
boundaries or labels. Add instances to the client's pack, upload, check and onboard through the same review,
sign-off and release steps. No setting or Terraform change is needed.

#### Declared join types, cardinality and validity windows

Each pack join requires `cardinality: one-to-one|one-to-many`, from its `from` side to its `to` side after the
declared condition, window and version policy apply. `one-to-one` means a source row matches at most one target;
`one-to-many` means it may match several. Include the context's current or as-of policy when checking a versioned
target; historical versions can repeat the same key. A sum or count repeated by a one-to-many join is refused by the compiler
and SQL gate unless the measure is aggregated at its own grain in a subquery or CTE before joining.
An explicit type that would discard a missing optional role required by a null-keeping exclusion is refused;
declare `left` or `full` for that role.
Each join may declare `type: inner|left|right|full`; omission keeps `inner`. Optional `validity_window` names
three fully qualified date columns, `{date, effective, expiry}`, and adds inclusive `date BETWEEN effective AND expiry`
to the join's `ON`. The effective and expiry bounds belong to the same joined side; the date belongs to the other
side or a table the context has already reached. For example:

```yaml
joins:
  - id: event_member
    kind: key
    from: ledger.events
    to: ledger.members
    condition: events.member_id = members.member_id
    cardinality: one-to-one
    type: left
    validity_window: {date: ledger.events.occurred, effective: ledger.members.effective, expiry: ledger.members.expiry}
```

The pack check rejects missing or unknown cardinality, unknown join types, undeclared or non-date window columns
and duplicate declarations.
Different named joins may declare distinct types or windows on the same keys. Both reference SQL and served data
queries must use the declared type, exact key condition and complete window. Undeclared joins, changed conditions,
removed or altered windows and derived-table outer joins are refused. Upload the complete pack and onboard, review,
sign off and release it through the usual input loop; no image setting or Terraform change is needed.

#### Calendars, weeks and empty periods

A pack calendar accepts optional `week_start`: `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday` or
`sunday`. The default is Monday. Optional `spine` names a declared day table and DATE column:

```yaml
calendars:
  - id: calendar
    year_start_month: 1
    quarter_label: "{yyyy}-Q{n}"
    week_start: monday
    spine: {table: ledger.calendar_day, column: day}
```

Populate the table with one row for every day across the supported range. The DATE column must be its complete
primary key in the lock; `kge check-pack` verifies the table, type and key. Day, week, month, calendar quarter/year
and fiscal quarter/year breakdowns derive their keys from that date and the declared calendar. A bounded breakdown
includes every period in the requested range, even with no observations. An unbounded breakdown fills gaps from its
first through last observed periods.

"By week" and "weekly" return week-start dates. Monday weeks follow ISO boundaries: 30 December 2024 belongs to
week 1 of 2025. Derive the week-year from the date, never from `YEAR_VAL` paired with `WEEK_NUMBER`. A month-grain
measure cannot be broken down by day or week.

On a physical measure, optional `empty: zero` or `empty: 'null'` determines the value for a period with no
observations. Quote `'null'` so YAML keeps text. The period remains a row. Omission keeps existing behaviour:
bounded released additive measures show zero; other missing observations remain null.

Run `kge check-pack`, upload the complete pack through [A client from its own fixture](#a-client-from-its-own-fixture),
then onboard, review the candidate, sign off and release. Adding or changing these declarations needs no new setting
or image change.

#### Declared aggregations and formulas

A physical measure may now declare an optional `aggregations` list using `sum`, `count`, `avg`, `min`, `max` and
`median`. For example, add `aggregations: [sum, avg, min, max, median]` beside its default `aggregation: sum` when
the business definition permits those operations. The list must be nonempty, unique and include the default.
Without it, only the existing aggregation is allowed. Alternatives keep the declared row expression and optional
`FILTER`; compound or multiple-aggregate expressions, `DISTINCT` and `*` cannot declare alternate operations.
`kge check-pack` reports invalid declarations before upload.

Questions can request "average" or "mean", "minimum", "smallest" or "lowest", "maximum", "largest" or
"highest", and "median". An undeclared choice is refused. With a breakdown, "highest" and "lowest" retain their
ranking meaning. Median uses the exact central non-null observations and interpolates as `FLOAT64`, with average's
numeric precision. Each output owns its aggregation. A question-wide operation is only the default for outputs that
name none; a count alongside a sum cannot change the sum's operation.

A named formula goes in the optional `derived_measures` section. With the glossary term and physical input measures
already declared, an example is:

```yaml
derived_measures:
  - id: ledger.net_income
    term: net_income
    formula: {kind: difference, numerator: ledger.income, denominator: ledger.cost}
```

The id must be unique across physical measures, count metrics and derived measures. The term names a glossary id. The formula's kind is
`ratio`, `difference`, `percent_change` or `share`; both operands name declared physical measures or count metrics. Asking for the glossary
name uses that exact formula and each input's default aggregation, filters and time rules. Inputs need compatible
populations and currencies and an unambiguous context. Nested formulas are refused; ambiguous contexts prompt a
clarification. A zero denominator returns null with `zero_denominator` for a ratio or percent change, and a missing
input remains null.

A formula may bind an input population explicitly with `contexts: {numerator: income_context, denominator: cost_context}`
beside `formula`. Each context must declare the corresponding physical input measure. A count operand keeps its
count metric's declared population. For a share, both context bindings name the same population. Bound populations
are used even when the person asks only for the metric's name; a model cannot substitute another context. Leave the
bindings out only when the input populations are unambiguous.

A distinct entity count is a declared metric in `count_metrics`. Give it a unique `id`, a glossary `term`, and an
`entity` whose complete key is declared. An optional `context` fixes the counted population to that context's fact,
filters, joins and version rules; without it, the population is the entity's own table. Optional `filters` apply to
this count output only, each with a qualified `column`, `op` and comparison `value`. The closed operators are `eq`,
`ne`, `gt`, `ge`, `lt`, `le`, `contains`, `starts_with`, `is_null` and `is_not_null`; null tests take no value.
Text matches require a text column and a literal under the same rules as terminology predicates. A count
declaration grants its local text filters only to that count output. Local filters read the entity's table and
cannot read restricted columns. For example:

```yaml
count_metrics:
  - {id: branch_household_count, term: branch_household_count, context: branch_sales, entity: account_household,
     filters: [{column: ledger.account.household_id, op: gt, value: 0}]}
```

A count needs its declared metric even when the question names the entity rather than the metric's glossary name.
An undeclared count or an ambiguous count population is refused with a reason. Sum and count outputs keep separate
aggregations; asking for one cannot change another output's operation.

A share is declared in `derived_measures` with `formula: {kind: share, numerator: ledger.income, denominator: ledger.income}`.
Both operands name the same metric: the denominator removes the requested breakdown and retains the filters. A ratio,
share, difference or percent change can also name a declared count metric as an operand. Structural share and ratio
wordings select those declarations; they cannot invent a metric that the pack lacks. The count and share wordings,
like all other business wordings, belong in the pack and reach the candidate through onboarding.

Deploy the shipped KGE image, then add these declarations to the client's pack and upload it through
[A client from its own fixture](#a-client-from-its-own-fixture). Onboard, review the candidate, sign off and release.
No new setting or Terraform change is needed.

#### Shares, cumulative figures and comparisons

The upgraded image also accepts these question shapes over declared measures and breakdowns:

- "share of NNA by region for Q1 2025", or "NNA by region as a share of total" and "% of total". Each row shows its figure and percentage of the breakdown total. Named filters stay in that total.
- "running total of NNA by month for Q1 2025", or "cumulative NNA by month". A second breakdown restarts the running total for each of its values.
- "3-month moving average of NNA by month for Q1 2025". Each window averages the current month and up to two preceding months within the requested period; another breakdown restarts it per value. Missing observations make the affected windows unknown. Measures declare `moving_average_lengths` as distinct positive integers in periods, for example `[2, 3]`. Without a declaration, moving averages are refused; lengths absent from its list are also refused.
- "top 2 and bottom 2 branches by NNA for Q1 2025", or "top and bottom 2 branches by NNA". Each end is labelled and has the requested number of rows when enough values exist.
- "top 3 branches by NNA in each region for Q1 2025". The limit restarts within every declared region, with dimension values breaking ties consistently.
- "NNA for March 2025 as reported vs current", including "as originally reported" and "restated". The answer shows both figures and their difference. A measure without a declared release and freeze policy cannot supply the as-reported figure.

No new setting or pack edit is needed for measures that already declare the required dimensions, calendar and release policy.

#### A fourth portal app, Cerebro, on by default

The portal gains a fourth app, Cerebro, with two settings: `PORTAL_APP_CEREBRO` (`portal_app_cerebro`, default `on`) and `PORTAL_CEREBRO_NAME` (`portal_cerebro_name`, default `Cerebro`), described in [The image's settings](#the-images-settings). With the ALTA home on, the home shows a fourth door; set `PORTAL_APP_CEREBRO=off` to keep three.

**A single-app install acts before it upgrades.** With `PORTAL_KNOWLEDGE_HOME=off`, exactly one app may be on, and the new flag counts. An install that runs one app alone and has not set `PORTAL_APP_CEREBRO` has two apps on after the upgrade, and the new portal refuses to start, naming `PORTAL_APP_CEREBRO`. Switch Cerebro off first:

- **On Google Cloud.** Add `portal_app_cerebro = "off"` to the tfvars, initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), review `terraform plan`, which shows `PORTAL_APP_CEREBRO` and `PORTAL_CEREBRO_NAME` added to each client's `kge-portal-<client>`, and apply it. Only then deploy the new portal image. The installed portal image ignores both settings, so the apply changes nothing people see. A new image deployed before the apply gives a revision that fails to start, and Cloud Run keeps serving the previous one until the apply is made.
- **On your machine.** Add `PORTAL_APP_CEREBRO=off` to the Compose `.env` before running `docker compose up -d app portal` from the new kit's `compose/`.

An install with the ALTA home on needs no action: this kit's Terraform passes both settings with their defaults, and the image alone gives the same defaults. To roll back, deploy the installed portal image again; it ignores both settings.

#### Release and rollback are the knowledge engineer's

KGE's API now checks the Cerebro role matrix in front of each onboarding and steward action, and keeps separation of duties: the steward signs off, the knowledge engineer releases. After the upgrade, at the API and on the page:

- **Release and rollback are the knowledge engineer's only.** `POST /onboard/run` with step `release` or `rollback`, and `POST /onboard/rollback`, need a role with `building` (`knowledge_engineer` in the example) and the matrix's engineer row. A steward or admin who released before is refused with a 403; the job's own page-request check refuses them too. The job operator's `release` and `release PREVIOUS=yes` commands are unchanged.
- **Admins lose certify, sign-off and curation.** An `admin` holding `certification` is refused `POST /onboard/certify`, sign-off, and the steward queue, its gaps and its decisions. Access, identities and settings stay the admin's.
- **Each check is audited** in the role audit, read at `GET /access/role-audit`. A refusal by today's process check behind the matrix is audited too, and the read covers the newest entries of the last 31 days.

Before upgrading, make sure each client has at least one person granted `knowledge_engineer` (or another role with `building`) to release, and a steward to sign off; grant them in Access if not. No setting or pack change is needed; on Google Cloud, apply [The role audit's store grant](#the-role-audits-store-grant) and [The proposal reviews' store grant](#the-proposal-reviews-store-grant) too. To roll back, deploy the installed image again.

#### The role audit's store grant

On Google Cloud, from this kit, run `terraform apply` once. Initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan "${VARS[@]}"`: it shows one new grant per client on the store bucket, `roles/storage.objectCreator` for the client's service account, conditioned on objects under `access/<client>/role-audit/`, where KGE adds one role-audit entry for each call the role matrix checks and can never change or delete one. Apply it before deploying the new KGE image; the installed image ignores the grant, so the apply changes nothing people see. Without it the store refuses each entry, and every role-checked call (Ask, onboarding, the steward's actions and Cerebro) answers 503 "the role audit is unavailable". An install on your machine needs no step. Keep the grant if you roll back: an image from KGE kit 0.13.5 on writes the role audit, and applying an earlier kit's Terraform removes the grant.

#### The proposal reviews' store grant

On Google Cloud, from this kit, run `terraform apply` once, before deploying the new KGE image. Initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan "${VARS[@]}"`: it shows one new grant per client on the store bucket, `roles/storage.objectUser` for the client's service account, conditioned on objects under `clients/<client>/proposals/` and on the two objects `clients/<client>/pack.yaml` and `clients/<client>/review-upgrade.json`, and on nothing else under `clients/<client>/`. KGE writes there when a business owner accepts or a steward certifies an improvement proposal in Cerebro's Curate, when the certify that completes a proposal lands its pack, and on the first read of a review record written before kit 0.9.23. Without the grant the store refuses each of those writes: accept and certify fail, the proposal stays pending and every later improve run is refused with 409 "A proposal is awaiting review: accept, certify, run checks, sign-off and release it first."; and on a pilot holding such older review records, onboarding and status reads fail. The installed image makes the same writes, so the apply alone lets them through on it too, and changes nothing else people see. An install on your machine needs no step. Keep the grant if you roll back: applying an earlier kit's Terraform removes it.

#### Other changes within a line

- **Evaluation expectations on every runtime.** Every evaluation now computes an item's expected result from its approved reference SQL, run on the runtime and data the answer used, as [What an item is scored against](#what-an-item-is-scored-against) describes. An item with no approved reference SQL answered on a runtime other than the reference warehouse is a draft, and a reference SQL that cannot run on the answering runtime is reported as `expectation not available on <runtime>`, never as a failure or a known miss. A client served only by the reference warehouse evaluates as before. Nothing is asked of the platform team: deploy the new image. Context's known misses move at the next recorded baseline, which the next release records after its eval post passes; to move them sooner, run the `baseline` job.

- **Cerebro's default name.** Cerebro's default name is now `Cerebro`, in the portal's header, app switcher and ALTA home, instead of `Graph`. An install that sets `PORTAL_CEREBRO_NAME` keeps its own name. A Compose install whose `.env` was copied from the earlier kit's `.env.example` sets it to `Graph` already, so it keeps `Graph`: to take the new name, change that line to `PORTAL_CEREBRO_NAME=Cerebro`, or delete it, and run `docker compose up -d --force-recreate portal`. To keep the old name, set `PORTAL_CEREBRO_NAME=Graph`: on Google Cloud, set `portal_cerebro_name = "Graph"` in the tfvars before you apply; otherwise `terraform plan` shows `PORTAL_CEREBRO_NAME` changing to `Cerebro` on each client's `kge-portal-<client>`. On your machine, set it in the Compose `.env` and run `docker compose up -d --force-recreate portal`. To roll back, deploy the installed portal image and apply the installed kit's Terraform again.

- **Context Ask header.** The title and subtitle above Context's Ask are now portal settings: `PORTAL_CONTEXT_ASK_TITLE` (`portal_context_ask_title`) and `PORTAL_CONTEXT_ASK_SUBTITLE` (`portal_context_ask_subtitle`); unset or blank uses “Ask a governed question” and “Certified definitions and figures, answered only from data you may read.”, so the header reads as before. On Google Cloud, `terraform plan` shows both added to each client's `kge-portal-<client>`; apply it. Deploying the image alone keeps the same defaults. On your machine, set either in the Compose `.env` and run `docker compose up -d --force-recreate portal`.

- **Portal catalogue fields.** Deploy the portal image together with KGE before the next onboard or release. The served catalogue now includes `catalogue_state`, `portal_only` and `documents`. An older portal image rejects those fields and defers its catalogue sync. Published snapshots are stored at `clients/<client>/catalogue-publish/`.

- **An earlier test-drive install.** Kits 0.8.4 to 0.9.29 seeded `<role>.sample@example.com`. Updating images does not rename those people: use their existing usernames instead of the guide’s `<role>.<client>@example.com`, or start the scratch client over. For a pilot test-driven before the fixture, live access table and client-named people were introduced (for example, 0.9.19 to 0.9.32), use this order: deploy the new images, review and apply the new kit’s Terraform on the existing backend, then follow [Before a fresh test drive](#before-a-fresh-test-drive). Preview `teardown CONFIRM=<client> PEOPLE=remove PREVIEW=yes`, inspect its inventory and backup limits, then run `teardown CONFIRM=<client> PEOPLE=remove`. Run `test-drive CONFIRM=test-drive` and wait for its successful release before running `passwords ROTATE=all`. This route discards the scratch content and deployment setup; save anything you need first. Teardown keeps an existing live access table, so inspect its grants and retained usernames after the test drive; it does not rename existing people. Keep a working client’s content and accounts by following [Access migration for an installed client](#access-migration-for-an-installed-client) instead. An installation moving from 0.9 also follows [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal) for the portal changes, then [Upgrading from 0.10](#upgrading-from-010-context-docs-and-playbooks) for the Docs and playbook changes.

- **ALTA and table queries.** The portal now shows `<client> ALTA` (default “Client ALTA”), configurable with `PORTAL_PRODUCT_NAME` (`portal_product_name`, default `ALTA`) and `PORTAL_PRODUCT_LONG_NAME` (`portal_product_long_name`, default `ALTA: AI-powered Library for Trusted Assets`), and uses “curated dataset”. Keep `PORTAL_CLIENT_NAME` as the client’s name alone; `Everyone at <client>` stays the same. The portal names itself “ALTA: AI-powered Library for Trusted Assets”. `PORTAL_PRODUCT_SUBTITLE` (`portal_product_subtitle`) sets the Discover home subtitle; unset or blank uses “Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.” Run Query on table pages and its server endpoint now default to off. To enable them, set `portal_table_query = "on"` in the pilot tfvars and apply the reviewed Terraform change, or set `PORTAL_TABLE_QUERY=on` in the Compose `.env` and run `docker compose up -d --force-recreate portal`. Preview and its endpoints also default to off. To enable them, set `portal_table_preview = "on"` in the pilot tfvars and apply the reviewed Terraform change, or set `PORTAL_TABLE_PREVIEW=on` in the Compose `.env` and recreate the portal as above. Both settings take effect when the portal starts; `off` hides the corresponding tab and refuses its endpoints. With both off, table pages make no warehouse queries. Run Query uses catalogue fields for its column list and works with Preview off.

On BigQuery, the first onboard or release after this upgrade prunes graph generations, chunks tables and warehouse versions, including old sample v0 tables, that neither of the two retained release identities, the current pointer nor the newest completed candidate references. Historical release files do not extend retention. Rollback with `release PREVIOUS=yes` uses the previous retained identity and its original warehouse versions; an older pruned release cannot be rebuilt by selecting its hash.

Before an onboard that needs staging, set `TMP_URL=gs://<project>-kge-tmp` and grant bucket metadata access and read, write and delete access under `tmp/<client>/`. At 768 embedding dimensions, roughly 280 chunks can require staging; actual text, metadata and vector values determine the size. A planned load above the inline limit is refused at the start if staging is unset or inaccessible, before warehouse writes. See [The image’s settings](#the-images-settings) for the byte limit and checks.

For a fixture client, upload changed pack and fixture files as in [A client from its own fixture](#a-client-from-its-own-fixture), then run `onboard`, review its changes, sign off, release and restart the service. Do not run `lock` or `seed`: onboarding owns the schema refresh and fixture rebuild. The schema-only lock drops old row-count and storage fields once, so the first upgrade can change the model hash. Later row-only changes reload the same hash and still require a fresh check and sign-off. An image or identity-only upgrade that reports `current already` still needs `reload` where the steps below require it.

A kit's first two numbers are its line: 0.6.0, 0.6.1 and 0.6.2 are one line. A later kit of the installed kit's line upgrades it as follows; neither way touches a client's pack, people, units or releases.

- **On Google Cloud.** Follow steps 1 to 4 of *Upgrading from 0.5.0* below, with the installed kit wherever they name 0.5.0: check and load the new kit, push its image, initialise its Terraform on the same backend with the same variables, save each client's digest, current release and data, and deploy the new image to its job and service, then review `terraform plan` and apply it if it shows changes. Step 5 is 0.5.0's alone. To roll back, return each client's job and service to its saved digest and, if the apply changed anything, apply the installed kit's Terraform again.
- **On your machine.** Run the first commands of *Other upgrade and rollback operations* below, with `OLD_KIT` the installed kit's folder: they back up the store, copy `.env`, load the new image and start `gcs` from the new kit's `compose/`. Then run `docker compose up -d app` there, and nothing else from that section. To roll back, run `docker compose up -d app` from the installed kit's `compose/`.
- **To a kit whose question lexer reads its phrases from the graph (0.14 patch).** No setting is needed. The platform's preloaded standards move to version 1.3.0, which holds the question lexer's phrases, so every client's model hash moves once, even with an unchanged pack: the next `onboard` gives a new candidate, not `onboard: current already`. Check it, sign it off and release it as usual, then restart the service. Until then the served release reads the coded table, and no answer changes either way. A pack that declares in `phrases` a phrase the preload now holds (one of the platform's operation or filler phrases) is refused at that `onboard`, naming the phrase as a preloaded one: remove it from the pack, since the platform carries it, and onboard again.
- **To a kit with ODPS and ODCS exports, follow-up questions in Discover's Ask and Cerebro's compact answers (0.14 patch).** No setting or Terraform change is needed: deploy the new portal image. A data asset's page gains **Export as ODPS**, which downloads `<asset id>.odps.yaml`: an ODPS v4.2 document of the asset's catalogue entry as far as the person may read it, with what ODPS has no field for under `x-` extensions. Each use case card gains **Export as ODCS**, as [Catalogue attributes on an existing portal](#catalogue-attributes-on-an-existing-portal) describes. Discover's Ask now takes follow-up questions: each question sends back, unchanged, the history KGE returned with the previous answer, kept separately for each asset and domain; starting a new chat clears it, and once a conversation reaches KGE's eight turns the next question is asked afresh. Cerebro's chat shows each answer as a compact card: its label, its figure, table or chart, and its citations, with the full answer (notes, definitions, feedback, copy and cite) under **More**; a knowledge engineer's plan, SQL and trace are unchanged. To roll back, deploy the installed kit's portal image again.
- **To a kit with Cerebro's questions to try, admin-set Ask suggestions and `PORTAL_ASK_GUIDE` (0.14 patch).** No setting is needed: deploy the new portal image, which adds one `_portal_ask_suggestions` table to the portal's tables (on BigQuery, when the portal first reads its tables after the upgrade). Cerebro's chat shows "Try one of these" before the first question: the release's golden 20 questions the signed-in person may ask, as KGE serves them. Admin › Configuration gains **Suggested questions**: two lists, for a data asset's Ask and for a domain's, of up to 6 questions of up to 200 characters each, which replace Discover's standard questions from the next page load; a list left empty keeps the standard ones. The optional `PORTAL_ASK_GUIDE` (`portal_ask_guide` in Terraform, default `on`) hides Ask's Guidelines section when `off`; to pass it on Google Cloud, apply the new kit's Terraform. To roll back, deploy the installed kit's portal image again; the new table is left in place and unread.
- **To a kit whose Cerebro chat keeps Gemini's thought signatures (0.14 patch).** No setting or Terraform change is needed: deploy the new portal image. On Gemini 3 and later, Cerebro's chat failed with "The agent could not answer." after every tool call, because the next request lost the tool call's thought signature; the chat now sends each call back with its own signature, runs every call of a turn, and keeps thought text off the page. "Ask about this" now passes its node to the agent, which explores that exact node, refused as before where the person may not read it. Gemini 4 Argon (`gemini-4-argon`) is supported for `portal_ask_model` and for KGE's model tiers; on a Gemini 4 tier, KGE sends Ask's thinking as a level (`MODEL_<TIER>_THINKING_LEVEL`, `low` unless set) and never a token budget, so a `*_THINKING_TOKENS` setting that applies to Ask's calls on that tier, with no level set, stops the service at start, naming it. An install that set `portal_ask_model = "gemini-2.5-flash"` only to keep the chat working may set it back to a Gemini 3 or 4 model. To roll back, deploy the installed kit's portal image again.
- **To a kit whose Ask planner's schema Vertex AI accepts (0.14 patch).** No setting or Terraform change is needed: deploy the new KGE image. On 0.14.6 and 0.14.7, Vertex AI refused both of Ask's planner calls on Gemini 3.1 Pro, so AI-Generated answers did not come (rules answers were unaffected): the planner's schema named its whole vocabulary as an enum at every id field, which Vertex AI inlines to millions of input tokens, and the full plan's schema (`ASK_ORDER=model_first`) was too complex for Vertex AI to flatten. Now a vocabulary or reading id is a plain string in every schema Ask sends; the ids stay in the data the model reads, and the contract's checks and its corrections enforce them, a correction listing the ids allowed at the field (the 50 nearest the model's reply where there are more). On Gemini a filter's nesting is sent one level deep in a schema, so the planner's schema stays under 100 KB however Vertex AI inlines it. On Gemini 3 and later (Gemini 4 Argon included) the planner is now sent in JSON mode instead, as the full plan is, since Vertex AI still refused its schema with a bare "Request contains an invalid argument"; the schema in its system instruction keeps every level of nesting. On an older Gemini the cost of the one level is that the planner cannot write a junction, a not or a membership inside another ("(A and B) or C"), which no recorded plan does. Nothing detects a question that needed such a filter: the model can only abstain or write a flatter filter there, so check the reading the answer states. The full plan (`ASK_ORDER=model_first`) is sent in JSON mode on every provider, its schema in the system instruction, and checked in code. If a provider still refuses a call's schema (an HTTP 400 naming the schema or the input token count), that call is retried once in JSON mode, checked against the same contract, and the run trace records it (`schema`: `refused`, then `json` with `schema_fallback`); the retry is not one of the model's corrections, and no other refusal (access, a refusal by the model, a timeout or a budget) is retried. Recordings, rules answers and `config_hash` are unchanged; the planner's prompt digest in the run trace moves, as its schema text changed. To roll back, deploy the installed kit's KGE image again.
- **To a kit whose Ask model calls are structured (0.14 patch).** No setting is needed. Ask's planner, glossary classifier and wording now send their contract's schema as structured output, with their own budgets and thinking caps, as [The model](#the-model) describes; every other model call is sent as before. An install whose `app_env` sets `MODEL_MAX_TOKENS` or `MODEL_<TIER>_MAX_TOKENS` keeps it: it still replaces Ask's per-call budgets on that tier, and a value below 256 tokens (1,024 on a Gemini Flash-Lite model) on the tier Ask runs on now stops the service at start, naming the setting, where the model thinks (Gemini 2.5 and 3): raise it, or set `ASK_<CALL>_MAX_TOKENS` (the planner takes it in place of 16,384 tokens, the glossary classifier and the wording in place of 4,096), and thinking is now capped within it. Remove a value set only to make room for thinking, such as the `MODEL_HIGH_MAX_TOKENS = "16384"` set after #559, or set `ASK_PLAN_MAX_TOKENS` to change the planner's budget alone. Removing `MODEL_MAX_TOKENS` or `MODEL_MEDIUM_MAX_TOKENS` moves the medium tier's `config_hash`, so the document reader reads every document again, once; removing `MODEL_HIGH_MAX_TOKENS` does not. Setting one of the new budget or thinking settings enters the `config_hash` of the model Ask's calls are made on (the context agent's tier: high, else medium where high is unset, else the default model) and of no other model, so the document reader's tier keeps its `config_hash` unless Ask runs on that same tier. Left unset, nothing moves: recordings, the reader's cache and run hashes are unchanged. To roll back, deploy the installed kit's images again, as the steps above do.
- **To a kit with the tagger on medium.** The pilot’s `TAGGER_MODEL_TIER` default changes from `low` to `medium`. Follow the Google Cloud steps above and review `terraform plan`: unless `app_env` or your own `pilot_env` overrides the tier, it shows that environment setting changing on each client’s service and job. Apply this kit’s Terraform to take the new default; deploying the image alone keeps the old tier. To roll back, apply the installed kit’s Terraform again.
- **To a kit with the providers at 8.11.0.** The pilot's Terraform now requires `hashicorp/google` and `hashicorp/google-beta` 8.11.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes.
- **To a kit with the providers at 8.10.0.** The pilot's Terraform now requires `hashicorp/google` and `hashicorp/google-beta` 8.10.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes.
- **To a kit with the providers at 8.9.0.** The pilot's Terraform now requires `hashicorp/google` 8.9.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes.
- **To a kit on Python 3.12 with the providers at 8.8.0.** KGE's image is rebuilt on Python 3.12 (`python:3.12.15-slim-bookworm`), with its Python dependencies moved together, and the portal's with its npm dependencies updated; a client's pack, people, units and releases are read as before. The pilot's Terraform now requires `hashicorp/google` 8.8.0, which its `.terraform.lock.hcl` holds: initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`, which would move the lock), as the steps above do, then review `terraform plan`; the provider update alone changes no resource, so apply only if the plan shows changes. Upgrade the portal too, which the steps above do not: load, tag and push the new kit's portal image and set `PORTAL_IMAGE` and `PORTAL_DIGEST` as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does, then deploy it to each client's `kge-portal-<client>` as that section's step 3 does: its catalogue sync on BigQuery now sends each column's type, so a client whose first product scores 0.0 syncs (an install where `GET /api/products` returned nothing after a release syncs on the new revision's start, or within five minutes). To roll back, as above, and deploy each portal's previous image again: note it first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`.
- **To a kit whose portal and KGE pages show the client's display name.** Set the client's name once with `PORTAL_CLIENT_NAME`: Compose passes it to the portal and to KGE as `CLIENT_NAME`. It names the ALTA product, role guides and developer API reference. An install that upgrades without setting it shows "Client ALTA" and "Everyone at Client". On Google Cloud, set `portal_client_name` in the tfvars, for example `portal_client_name = "Meridian"`, review `terraform plan` and apply it: this makes portal and KGE service revisions. On your machine, set `PORTAL_CLIENT_NAME` in the Compose `.env` and run `docker compose up -d --force-recreate app portal`. To roll back, unset it the same way.
- **To a kit whose portal is built on Red Hat's UBI base (0.9.19 on).** The portal's image moves from Node's Debian slim base to Red Hat's signed UBI 9 Node.js 22 minimal base and runs as the same uid 1000, group 0, so its `portal-data` volume and its `_portal_*` tables are read as before. Upgrade the portal as well as KGE. On Google Cloud, note each client's portal image first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`, then load, tag and push the new kit's portal image as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does and deploy it to each client's `kge-portal-<client>` as its step 3 does; to roll back, deploy the noted image again. On your machine, load the new kit's portal image beside KGE's (`docker load -i images/kge-portal-@VERSION@.tar`) and run `docker compose up -d portal` with `app`; to roll back, run it from the installed kit's `compose/`. Where your registry admits only signed images, sign both images there before deploying them: [Pushing the images to your registry and signing them](#pushing-the-images-to-your-registry-and-signing-them).
- **To a kit whose job may run three hours.** Each client's job now has a three-hour timeout (`10800s`), up from one hour, so `onboard` and `test-drive` on a full pack finish; the page holds a request's `running` marker for 185 minutes before marking it `stale`. On Google Cloud the timeout reaches an installed job only through this kit's Terraform: `terraform plan` shows each `kge-<client>` job's timeout changing, so apply it. Until then the job keeps its one-hour limit and stops before such a run ends.
- **To a kit that reads documents in any format.** On Google Cloud, `terraform plan` shows `DOCUMENT_READER=gemini` added to each client's service and job, Vertex AI's service agent (`service-<project number>@gcp-sa-aiplatform.iam.gserviceaccount.com`) made through `hashicorp/google-beta`, which the pilot's `.terraform.lock.hcl` now holds too, and one new grant on the tmp bucket: `roles/storage.objectViewer` for that agent, conditioned on `tmp/`, so Gemini can read a document too large to send inline. Initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then apply it. Until then the image reads documents with the text reader, and PDFs and images onboard without passages. To roll back, apply the installed kit's Terraform again.
- **Model settings, before either.** A `TAGGER_PROVIDER` that names a provider other than `MODEL_PROVIDER` now needs its own model in `TAGGER_NAME` and, where that provider takes a key, its own key in `TAGGER_API_KEY` (`TAGGER_BASE_URL` for its endpoint): the tagger refuses to start without its model, naming the setting, and is never sent `MODEL_NAME` or `MODEL_API_KEY`, which are `MODEL_PROVIDER`'s. A `MODEL_<TIER>_PROVIDER` naming no known provider (a mistype) now stops each agent on that tier, naming it, instead of quietly falling back to rules; `none` there is kept as the switch that turns that tier's model off. With the settings the kit ships, which name no model, nothing changes.

### Upgrading from 0.13: question-shape sections in the pack (meta-model 1.6.0)

A 0.14 kit upgrades a 0.13 install by the steps in *Upgrading within a line* above, for KGE's and the portal's images. It preserves the client's pack, people, units, releases and Terraform state, and needs no migration, no new setting and no new secret.

What changes:

- **The pack format.** KGE's image is on meta-model 1.6.0, the version of the pack format it compiles against. It accepts six new optional pack sections, `phrases`, `shapes`, `recipes`, `clarifications`, `shape_tests` and `examples`, which declare question shapes as pack data, and an optional `part: true` on a `terminology.predicates` entry. The compiler checks them (rules S32 to S36), and refuses them in a pack whose `manifest.meta_model_version` is earlier than 1.6.0. A pack's `phrases` are read when a question is answered, by the question lexer's coverage check and its check of readable words: a phrase a pack adds can let a question through that was held as uninterpreted text, so add one only with the tests that show it reads as intended. The coded planner's own reading does not change, and nothing reads the other five sections yet. A pack that declares no `phrases` changes no answer. A pack written for meta-model 1.2.0 to 1.5.0 compiles as it is, with no edit. The question lexer marks a predicate a pack flags `part: true` as a measure part; nothing acts on the mark yet, so the flag changes no answer.
- **Business terms, codes and hierarchy.** A glossary term may also declare `is_a` (the terms it is a member of, by id), `broader` (the terms it sits under, by id) and `codes` (each a column and the code that identifies the term in it, several being alternatives). Each is a list. The compiler checks their shape, and refuses them in a pack whose `manifest.meta_model_version` is earlier than 1.6.0. It also checks their structure (rule S37): each names a declared term or a column the pack's lock holds and does not restrict, `is_a` and `broader` form no cycle, a term has one broader term in each classification, a coded column of a term that has a class, its own or a broader term's, is mapped to one of those classes, a term and its broader term coded on different tables have a declared hierarchy or key join between those tables, and no code or member name contradicts the pack's vocabulary. It compiles them into the graph as edges between existing terms and columns, never as new kinds of node. Nothing reads them when a question is answered yet. The up-front check gains `F13`, which checks them against the rows: each code is a value of its column, and each `broader` term agrees with the rows, in the fixture's CSVs on upload and in the live warehouse when onboarding compiles. A pack that declares no codes, as no 0.13 pack does, gets no new finding, sends no new query and prints nothing new.
- **The platform's preloaded standards.** They move to version 1.3.0 (format 2). Beside the industry standards' concepts, they now hold the platform's operations, its clarification texts and the names of its coded builders; baseline v1 of the platform's phrases, the operation and filler phrases the coded question lexer reads, which it now reads from the graph, with no change to any answer; an empty base set of shapes, recipes and clarification rules; and the fewest alternate phrasings a shape's test item has. A pack extends them and never redefines them.
- **Question-shape tests.** The up-front check gains `F12`, for the test items a pack's `shape_tests` cite: each is an item of its set file, with at least three alternate phrasings, and its reference SQL's approval, `reference_sql_approved_by` and `reference_sql_approved_on` on the set item, is both or neither, and given once a shape that tests it is above `specify`; an unapproved item whose shapes are all at `specify` is a draft warning. A pack that declares no `shape_tests` gets no new finding, and the model hash does not move for these.

The model hash:

- **The installed release keeps serving.** The release a 0.13 kit built is served by the new images as it is: KGE loads and serves an artifact compiled against an earlier meta-model of the same major version.
- **It moves once, for every client.** The next `onboard` compiles the pack against 1.6.0, so the model hash moves even when the pack is unchanged. Expect a new candidate, not `onboard: current already`: check it, sign it off and release it as usual, then restart the service. Its graph gains the question-shape tables, holding the platform's preloaded phrases and otherwise empty unless the pack declares those sections, one row for each of the platform's operations, one edge from each value and predicate vocabulary entry to its column, and two from each code/name pair, to its code and name columns.

To roll back, keep each image with the releases it built. A 0.13 image refuses an artifact compiled against meta-model 1.6.0 whenever its job reads one, to load it or to release it as a candidate: "it was compiled against meta-model '1.6.0', which this platform, on meta-model 1.5.0, does not load". It does not compile a pack written for 1.6.0 either. Serving does not check the meta-model again, so a 0.13 service started on a release built on 0.14 would serve it, reading only the tables it knows. So, in this order:

1. **Make the release built on 0.13 current again, with the 0.14 job.** Skip this step if no release was made on 0.14. While the 0.14 images are still deployed, roll back as [Release, rollback and baseline](#release-rollback-and-baseline) describes: `"${JOB[@]}" --args="release,PREVIOUS=yes"` on Google Cloud, or `docker compose run --rm job release PREVIOUS=yes` from the new kit's `compose/` on your machine. If more than one release was made on 0.14, select the release built on 0.13 by its hash instead, with `release HASH=<hash>` (`--args="release,HASH=<hash>"` on Google Cloud), while it is still retained. The rollback restores the pack that release was built from.
2. **Restore the images** as *Upgrading within a line* describes, then restart the service.

### Upgrading from 0.12: certified answers first, AI-Generated fallback

A 0.13 kit upgrades a 0.12 install by the steps in *Upgrading within a line* above, for KGE's and the portal's images. It preserves the client's pack, people, units, releases and Terraform state, and needs no migration and no new secret. It needs two steps of its own on Google Cloud:

1. **Terraform: the feedback grant.** Initialise the new kit's `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan "${VARS[@]}"`: it shows one new grant per client on the store bucket, `roles/storage.objectCreator` for the client's service account, conditioned on objects under `feedback/`, where `POST /ask/feedback` and the `feedback` tool add one object per verdict and can never change or delete one. Apply it. Until then a feedback call is refused with 503. To roll back, apply the installed kit's Terraform again.
2. **The verified examples.** After the new image is deployed to the job, run `"${JOB[@]}" --args=baseline` once for each client: it records the baseline as before and writes `baselines/examples.json` beside it, the golden questions the current release certifies with their plans, which an AI-Generated answer's prompt reads. Every later release writes it again. A service instance reads the file once per release, so then start a new revision of each client's service as [Passwords and the service](#passwords-and-the-service) does after a release (`gcloud run services update kge-<client> --region=$REGION --revision-suffix="examples-$(date +%s)"`); until then its AI-Generated answers are made with no verified examples. On your machine run `docker compose run --rm job baseline`, then `docker compose restart app`, from the new kit's `compose/`.

Two KGE settings are new; [The image's settings](#the-images-settings) describes them. Their defaults turn the new behaviour on:

| Setting | Default | How to set it |
|---|---|---|
| `ASK_ORDER` | `rules_first`: the rules answer first, certified, with no model call | `app_env` on Google Cloud |
| `ASK_AI_FALLBACK` | `on`: where the rules cannot answer, the model's plan, labelled AI-Generated; `off` without a model | `app_env` on Google Cloud |

To keep 0.12's order, set `ASK_ORDER = "model_first"` in `app_env`; to give certified answers only, set `ASK_AI_FALLBACK = "off"`. Then apply the kit's Terraform as for any `app_env` change. `GET /health` now reports, in `ask`, the fallback in effect, the order and the model, and every answer names its `order` beside `answer_mode`. To roll back, restore the images as *Upgrading within a line* describes and remove the two settings from `app_env`.

### Upgrading from 0.11: portal Ask fallback, Search and data quality filters

A 0.12 kit upgrades a 0.11 install. It preserves the client's pack, people, units, releases and Terraform state. Apply any relevant changes in *Upgrading within a line* above for the installed kit, then upgrade KGE's images by those steps and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal) step 2. The changes are the portal's; KGE's image moves with it, as within a line.

Ask now falls back in one order, `kge` → `portal` → `none`, starting at `PORTAL_ASK_PROVIDER`: KGE, then the portal's catalogue agent, then the catalogue rules. `none` now names the rules-only layer, which gives the same owner and fields answers as before and matches a product's name loosely. Search opens its results page as soon as a query is entered. A table's Data quality tab lists rows with a rule before rows without one, and `PORTAL_DQ_FILTERS=off` hides its four filter buttons.

Four portal settings are new; [The image's settings](#the-images-settings) describes them. Their defaults keep today's behaviour:

| Setting | Default | Terraform variable |
|---|---|---|
| `PORTAL_ASK_PROVIDER` | `kge` with a KGE client, `portal` alone; Terraform passes `kge` | `portal_ask_provider` |
| `PORTAL_ASK_MODEL` | unset: the `portal` layer passes straight to the rules, with no model call | `portal_ask_model` |
| `PORTAL_ASK_MODEL_PROVIDER` | `vertex` | `portal_ask_model_provider` |
| `PORTAL_DQ_FILTERS` | `on`: the four buttons shown, opening on All | `portal_dq_filters` |

To use the portal's catalogue agent, set `PORTAL_ASK_MODEL` to a Vertex AI model id, for example `portal_ask_model = "gemini-2.5-flash"` in the tfvars. The portal runs as its client's KGE service account, which the kit's Terraform already grants `roles/aiplatform.user`, so no IAM change is needed. The agent reads only the person's readable catalogue within the page's scope, never data or figures. On your machine the portal has no Google credentials, so leave `PORTAL_ASK_MODEL` unset there.

On Google Cloud, apply the new kit's Terraform to pass the new settings: initialise its `terraform/pilot` with `terraform init` on the same backend (not `-upgrade`), then review `terraform plan`, which shows the four settings added to each client's `kge-portal-<client>`, and apply it. Deploying the image alone keeps the portal's built-in defaults, which are the same. On your machine, set any of them in the Compose `.env` and run `docker compose up -d --force-recreate app portal`.

Keep the installed 0.11 kit and the previous image references until the upgrade is checked. To roll back, restore KGE's images as *Upgrading within a line* describes and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal)'s rollback steps, then apply the installed kit's Terraform again. On your machine, use the installed kit's Compose folder as those steps describe.

### Upgrading from 0.10: Context Docs and playbooks

A 0.11 kit upgrades a 0.10 install. It preserves the client's pack, people, units, releases and Terraform state. Apply any relevant changes in *Upgrading within a line* above for the installed kit, then upgrade KGE's images by those steps and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal) step 2. Upgrade both services so the portal's Docs entry and the served guides agree. The Docs and playbook changes require no additional Terraform change or new setting.

Context's global bar now opens its own Docs: page-only guides for knowledge engineers, business teams and stewards, plus the official API and MCP connection guide for developers and agent builders. The guides follow the person's existing permissions. Tools and the SQL check no longer appear in Context; the SQL check remains available through the API.

The kit carries only the installation playbook, `guides/operator.md`, and its runner. Use `docs/ONBOARDING.md` to find it. End users follow Context's Docs for their daily work. Executable development playbooks remain in the repository and are no longer shipped in the kit.

Keep the installed 0.10 kit and the previous image references until the upgrade is checked. To roll back, restore KGE's images as *Upgrading within a line* describes and the portal's image by [Upgrading from 0.9](#upgrading-from-09-context-in-the-portal)'s rollback steps. On your machine, use the installed kit's Compose folder as those steps describe.

### Upgrading from 0.9: Context in the portal

A 0.10 kit upgrades a 0.9 install. It doesn't touch a client's pack, people, units, releases or Terraform state, and it needs no Terraform change. Upgrade the portal as well as KGE: Context's page now lives in the portal, so people reach it only through a 0.10 portal.

1. **KGE.** Follow *Upgrading within a line* above with the 0.9 kit as the installed one.
2. **The portal.** On Google Cloud, note each client's portal image first, from `gcloud run services describe kge-portal-<client> --region=$REGION --format='value(spec.template.spec.containers[0].image)'`. Then load, tag and push the new kit's portal image as [Upgrading from 0.8: the discovery portal](#upgrading-from-08-the-discovery-portal) step 1 does, and deploy it to each client's `kge-portal-<client>` as its step 3 does. On your machine, load the new kit's portal image beside KGE's (`docker load -i images/kge-portal-@VERSION@.tar`) and run `docker compose up -d app portal` from the new kit's `compose/`. Where your registry admits only signed images, sign both images before deploying them: [Pushing the images to your registry and signing them](#pushing-the-images-to-your-registry-and-signing-them).
3. **Where people find Context.** People open Context from the portal, at its `/context` address, after signing in. The service’s `/` always shows the developer page; APIs, MCP, `/onboarding-guide` and `/docs` remain available. Tell people the portal address before the upgrade.

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

Use cases say what assets are used for. Each is defined once, in a top-level `use_cases` list in `catalogue.yaml`, and each product lists the ids of its use cases, in the order it shows them. A definition has an `id` (text with no spaces, such as `uc-month-end-revenue-pack`, given once in the list), a `title` and an `objective` (one or two lines), all required, and may add a `domain`, an `owner` (a role, not a person's name), a `url` (an address starting `https://`), a `kpi` (a `name`, required, and an optional `definition`, `grain` and `unit`) and `example_questions` (a list of questions the linked assets can answer); no other key is accepted. The title, objective and url are the Open Data Product Specification's use-case fields (ODPS v4.0 `useCaseTitle`, `useCaseDescription` and `useCaseURL`). For example:

```yaml
use_cases:
- id: uc-month-end-revenue-pack
  title: Month-end revenue pack
  objective: Finance reports each month's interest and fee revenue to management after the close.
  domain: Retail Banking
  owner: Retail Finance Controller
  url: https://example.com/reports/month-end-revenue
  kpi:
    name: Monthly retail revenue
    definition: Interest and fee income booked to retail accounts in the month, converted to US dollars.
    grain: Month
    unit: USD
  example_questions:
  - Total retail revenue for June 2025
products:
- id: retail.monthly_revenue
  use_cases:
  - uc-month-end-revenue-pack
```

The pack check refuses a definition that breaks these rules, an id defined twice (`catalogue.yaml:use_cases[2].id: uc-month-end-revenue-pack is defined twice`), a product that lists an id the list does not define (`... retail.monthly_revenue names unknown use case uc-x`) and a product that lists the same id twice, each finding naming the id; it warns, without refusing, when an asset lists more than three. A product may still give an entry inline, as before this release: a `title`, a `description` and, optionally, a `url`, checked as before. It is accepted, with a warning naming the asset, for one release, and is shown as a use case whose id is `uc-` followed by its title in lower case with hyphens (or, for a title with no letter a to z or digit, its characters' code points in hex), and whose objective is its description. Moving each inline entry into the `use_cases` list and listing its id is recommended now; a release published before this one, with entries in the older shape, is still served.

Discover's home shows "Browse by use case" directly above "Browse by community": one card per use case, counting only published assets the person may read. Each is drawn by the same card as a data product: "Use case" and its domain above its title, its owner, its objective, its KPI's name, and how many linked data assets the person may read ("1 asset", "3 assets"). Clicking the card, or pressing Enter or Space on it, opens Discover's search filtered to that use case's assets; there is no detail view. Its **Export as ODCS** button, which does not open the search, downloads `<use case id>.odcs.yaml`: an ODCS v3.2.0 data contract (ODCS's newest version; it has no 4.x) of the use case and the linked assets the person may read. A use case with no asset the person may read is not shown, and the section is hidden when no visible asset has a use case. On the asset's Overview tab, a Use cases card under About this data asset shows the first three in the order listed, each link opening in a new tab, with "All n use cases →" opening the Use cases tab when there are more, and reads "No use cases recorded yet" for an asset with none. The Use cases tab, directly after Overview and shown only for an asset with use cases, shows the card of each of its use cases, each opening the same filtered search. Use cases are published with the release, or with `kge onboard portal`, and the portal shows them at its next catalogue refresh; a product that lists no use cases has none, so removing all of an asset's use cases takes effect at the next `kge onboard portal` or release, and `kge rollback portal` to a catalogue published without use cases shows none, whatever the graph release lists; they are changed only in `catalogue.yaml`, never on the page. The portal keeps them in the `use_cases` column of its products table, as before: no setting, Terraform change, table recreation or manual DDL. Deploy the portal image and KGE's together: from the upgrade on, KGE serves use cases in the new shape, which an older portal refuses, keeping its last catalogue.

### Upgrading from 0.8: the discovery portal

A 0.9 kit upgrades a 0.8 install, and adds the discovery portal to it. It is written for an install on the newest 0.8 kit: an install on an earlier one also takes that kit's changes on the way, so read *Upgrading from 0.8.0 within the 0.8 line* below first (a pilot on 0.8.0 also gains the tmp bucket in step 2's plan), or upgrade to the newest 0.8 kit before this. It does not touch a client's pack, people, units, releases, Terraform state or passwords, and KGE answers as before. On the pilot it adds one Cloud Run service per client, `kge-portal-<client>`, running as the `kge-<client>` service's account, and the image repository gains a second image, `kge-portal`. No account, dataset or grant is added. The portal creates its `_portal_*` tables in the client's existing warehouse dataset when it first starts.

An existing install gets a catalogue by running onboard, sign-off and release: KGE derives every product from the pack, lock and model roles, and the portal works from onboarding alone. Uploading `catalogue.yaml` beside `pack.yaml` and `pack.lock` is optional; it enriches descriptive fields and is validated against the governed tables, columns and roles. The portal syncs the release without replacing favourites, requests, feedback, roles or publish status.

`catalogue.yaml` is the source for descriptive fields (path, format, badges and the other card text) and for portal-only cards. Columns, glossary terms, lineage and certified figures still come only from a graph release.

| Command | What it does |
|---|---|
| `kge onboard` | Today's full checks and the gate, then one sign-off and release. The graph is released first and the catalogue publishes with it. A failed gate publishes nothing. |
| `kge onboard portal PRINCIPAL=<steward or access admin>` | Checks `catalogue.yaml` only and publishes it after one approval by a steward or an access admin. Omit `PRINCIPAL`, or set `PREVIEW=yes`, to print the card diff and write nothing. With a principal, the diff is printed and then the catalogue is published. The portal shows it at the next catalogue refresh. No graph build, no gate and no KGE restart. An entry for a graph asset may change descriptive fields only; a governed fact is refused by name. The pack content role `admin` is not an approver. |
| `kge onboard graph` | Today's graph checks, sign-off and release. Descriptive fields stay as last published. Governed facts refresh as they do today. |
| `kge rollback portal PRINCIPAL=<steward or access admin>` | Restores the previous published catalogue. The graph version is unchanged. The first portal publish keeps the graph release's descriptive cards as that previous snapshot when none was stored yet. |
| `kge served` | Prints the graph hash the API is serving and each card's state: In sync, Portal ahead, or Not in the knowledge graph. |

A portal-only card is a document, a document set or an API that is not in the graph. It needs a unit, an object type (`Documents` or `API/MCP`) and its descriptive fields. A document set lists fixture document ids, or names a shelf. People see the card only when they may read its unit. Ask does not answer figures from it, and Search does not answer from it. Every portal publish and rollback is audited with the person who approved it.

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

Since 0.5.0, the kit adds page-led unit onboarding. Terraform gives each service `roles/run.invoker` on its own job, conditioned `roles/storage.objectUser` grants on `clients/<client>/units/`, `clients/<client>/onboard/` and `clients/<client>/proposals/` and on the two objects `clients/<client>/pack.yaml` and `clients/<client>/review-upgrade.json` (never the whole of `clients/<client>/`), and `RUN_REGION`; it changes the job's default argument from `onboard` to `onboard-request`. The service cannot override the job's command or environment. The job keeps the same unconditional, store-wide `roles/storage.objectAdmin` grant as 0.5.0. It does not replace Terraform state, the store, releases or passwords. Check the model settings first, as *Model settings, before either* under *Upgrading within a line* above says.

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

4. Run `terraform plan "${VARS[@]}"`. The plan adds the service's job `roles/run.invoker`, its conditioned store `roles/storage.objectUser` grants (on `clients/<client>/units/`, `clients/<client>/onboard/` and `clients/<client>/proposals/`, and on the objects `clients/<client>/pack.yaml` and `clients/<client>/review-upgrade.json`), and `RUN_REGION`, and changes the job arguments to `onboard-request`. The job's store-wide `roles/storage.objectAdmin` grant is unchanged from 0.5.0. Review the plan, then run `terraform apply "${VARS[@]}"`.
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

### Long figure questions

Context submits graph-arm Ask questions as background runs. The Knowledge API keeps the synchronous JSON contract for existing clients; opt in with `POST /ask?background=true`, then follow `GET /ask/runs/{run_id}` with the same signed-in identity. The 202 response contains `id`, `state`, `poll_after` and `expires_at`; a completed run includes `answer`. Both routes also accept `Accept: application/x-ndjson` for live events: the stream starts with the run (its `id`, `state` and `cursor`) as soon as the run has an id, then sends each event as the question reaches it, at its cursor: each stage as it starts and, with its time, as it ends (`stage`), each graph node and edge once (`node_added`, `edge_added`) and a later change as its own event (`state_changed`, `edge_changed`), the plan and the context once, and warehouse progress. A stream ends with the answer, or when it stops waiting for the warehouse; reconnect by id, passing `after=<cursor>` to receive new events, which a poll that advances the run streams as they come. Closing a connection leaves the warehouse job running. Reconnecting never submits another job. A changed release or removed access refuses the saved answer; ask again against the current release.

Run records use `QUESTION_STATE_BACKEND=store` by default, private objects in `questions/<client>/runs/` in the shared store bucket. Each service can write only its client's run prefix; apply the kit's Terraform before deploying this image. Optional `QUESTION_STATE_BACKEND=bigquery` uses transactional records in the existing graph dataset. Both keep the id, principal, checked plan checkpoints, warehouse handles and answer across API instances. The local warehouse uses an in-process worker; a local process restart requires asking again. The pilot's BigQuery jobs continue on Google's side without an API worker or a Cloud Run job per question.

| Image setting | Default | Allowed values |
|---|---|---|
| `QUESTION_STATE_BACKEND` | `store` | `store` (shared bucket or folder), `bigquery` |
| `QUESTION_RUN_TIMEOUT` | `900` seconds | positive, below 1200; whole question lifetime |
| `QUESTION_POLL_SECONDS` | `2` seconds | positive, below 60; the interval a background question asks its client to poll at, and the least time between warehouse status reads after the first response |
| `QUESTION_START_WAIT` | `5` seconds | 0 or more, below 60; how long the first response follows the warehouse, reading job status at most about twice a second, before answering in the background; 0 answers with the id at once. A live (`application/x-ndjson`) first response sends the run's id at once and its events as they come throughout |

`BIGQUERY_JOB_TIMEOUT` configures each warehouse job's timeout (background default 600 seconds, capped by the whole run lifetime). The existing `MODEL_TIMEOUT=180`, `MODEL_TOTAL_TIMEOUT=270` and `MODEL_REQUEST_TIMEOUT=270` bound model attempts, all model retries and each short request respectively. The API and Context Cloud Run request limits are 1200 seconds, above the supported question lifetime; warehouse waiting is carried across short requests. An over-limit run returns a clear time-limit message. Planning, access checks and gates run before submission. The first response waits up to `QUESTION_START_WAIT` for the submitted warehouse jobs, so a short question returns its answer with its id; a longer one returns its id and continues in the background. The service refuses to start when a question setting is out of range. Fetching status advances completion, so keep polling until `done`, `failed` or `expired`. Each completed answer's `run_trace.warehouse_jobs` records statement time in milliseconds, bytes processed and slot milliseconds. The stream also reports those metrics as progress.

Size the API with Terraform's `requests_per_instance` (default 80), `min_instances` (installer setting, default 1) and `max_instances` (pilot default 10). Validate one instance with several jobs lasting over five minutes, then repeat at 5–10 instances: ask through Context and the API, reconnect on another instance, and check the same answer and job ids. Set a shorter `QUESTION_RUN_TIMEOUT` for an over-limit check and confirm the explicit message. Slot and model usage are the client's costs; these are acceptance checks to run on the pilot.

The kit creates no BigQuery reservation by default (`bigquery_create_reservation = false`); queries use the execution project's existing billing assignments. Before enabling the GCP-native graph path, confirm that the project has an Enterprise or Enterprise Plus assignment supporting its GQL queries. If the installer wants this kit to supply that capacity, set `bigquery_create_reservation = true` in `terraform/pilot/example.tfvars` (or the installer’s own tfvars) and review the capacity charges before applying. This creates an Enterprise reservation with a zero baseline, autoscaling up to `bigquery_max_slots` (default 50), and assigns the project's QUERY jobs to it. Autoscaled slots are billed under [BigQuery capacity pricing](https://cloud.google.com/bigquery/pricing#capacity_compute_analysis_pricing), including its minimum billing periods; the slot cap does not cap storage, model or other charges. Leave the variable false when a reservation managed outside this kit supplies the required edition. When upgrading an older kit that already manages its reservation, set the variable true before applying to retain it; false plans removal of that reservation and its assignment. For heavy measures, the client can choose partitioning, clustering or pre-aggregated tables and certify the corresponding runtime bindings; the kit does not reshape client tables.
