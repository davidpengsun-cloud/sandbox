# Installation playbook

Page: open `/context` at the portal’s address, sign in with your email and password, and choose Context from the app switcher. Docs opens Context’s end-user guides. The service’s `/` is the developer page. APIs and MCP remain available.


Use your operator login, for example operator.{{client}}@example.com. Cloud job execution is operator power; it does not grant a Context role. Read this guide from the kit; it is separate from Context’s Docs. Install and start the service first, following INSTALL.md.

This is the installation playbook, separate from Context’s end-user documentation and the dev team’s executable playbooks. Run `kge playbook operator` in the selected environment; read the whole guide first. This scratch example uses the selected client’s test-drive people. The runner substitutes `{{client}}` in commands and expected output; when following the guide by hand, replace it with your installed client id. For an existing installation with different people, use a working copy with those usernames.

Every numbered step has a page way and a `kge` way. `[mutating]` changes application or local credential state; `--safe` skips it and requires an already connected environment; it never signs in or starts a proxy. `--dry-run` prints commands without executing any command or checking output. A `capture:name=pattern` expectation saves the first matched group for later `{{name}}` arguments; `different:first,second` checks that two captured values differ. Values substituted into `regex:` expectations are matched literally. Each other `expect` line is a literal substring of that step's combined stdout, except `regex:` lines, which use Python regular expressions with multiline matching. A nonzero command or missing expectation stops at the named step. Arguments are passed directly, without a shell. `{{env}}`, `{{client}}` and `{{folder}}` come from the selected environment; the scratch run defaults to UNIT=leasing and the shipped Meridian upload. `{{examples}}` locates the shipped examples. Override `UNIT=`, `PROPOSAL=` or `UPLOAD=` for your own work. A broken step fails acceptance. `--from-step N --through-step N` runs a numbered range for a human handoff; omitted bounds mean the first or last step. A `Targets:` line limits a step to the named targets; other targets print a skip. `{{decision}}` uses PROPOSAL= when supplied, otherwise UNIT=; read and choose that review target before execution.

Before the first connection, the operator registers the environment: `kge add local` from the local demo folder, `kge add compose /path/to/compose`, or `kge sync` on GCP. Select it with `kge use local/{{client}}`, `kge use compose/{{client}}` or `kge use gcp/{{client}}`. GCP discovery is read-only; `kge setup` also prompts for missing settings. `kge config` shows the saved settings; state holds no secrets.

## 1. Connect [mutating]

Page: sign in with your email and password. The Docs section opens the guides for your live access-table roles. Command line: `kge connect <email>` switches to that person if needed and prompts privately for their password. It starts `gcloud run services proxy` on GCP, then signs in if there is no valid token. Your Google account needs `roles/run.invoker`. Local and Compose targets use their local URL without a proxy. `kge proxy`, `kge login`, `kge logout` and `kge proxy stop` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect; `kge config AUTO=off` disables that.

```kge
kge connect operator.{{client}}@example.com
```
```expect
regex:^connected: (local|compose|gcp)/{{client}} as operator\.{{client}}@example\.com \((?!None\b|null\b)[a-z][a-z0-9_-]*(?:, (?!None\b|null\b)[a-z][a-z0-9_-]*)*\)$
```

## 2. Check the selected environment [read-only]

Page: check the profile in the page header. Command line: inspect the saved target and client before any write. `kge use {{env}}` selects the same environment persistently; `ENV=local/{{client}}` selects one command only. On GCP, repeat `kge sync` to refresh discovered service, job, buckets and secret reference; it prints new, changed or unchanged without changing cloud resources.

```kge
kge config
```
```expect
regex:^TARGET=(local|compose|gcp)$
regex:^CLIENT={{client}}$
```

## 3. Establish access admins [mutating]

Page: there is no operator control; the access admin asks the operator to run this command. The list replaces the access-admin membership, so retain every existing admin. This example is for a scratch test-drive installation. Only `kge admins` (the job's `access ADMINS=`) writes the reserved internal role `access_admin`, displayed as **Access admin**. `ACCESS_ADMINS` remains a bootstrap union. The ordinary pack role `admin` is not this privilege. Context access admins are portal admins too.

```kge
kge admins access_admin.{{client}}@example.com BY=operator.{{client}}@example.com
kge access LIST=yes
```
```expect
regex:"admins":\s*\["access_admin\.{{client}}@example\.com"\]
regex:"changed_outside":\s*false
```

## 4. Generate and deliver passwords [mutating]

Page: no password-generation button; the operator privately gives each person their new password. Command line: merge against the current hashes; existing passwords stay unchanged, removed people disappear, and new people get a password. The first run needs `kge passwords ROTATE=all` when there is no merge base. `ROTATE=all` deliberately rotates everyone; never use it just to add a person.

```kge
kge passwords
kge restart
```
```expect
regex:^.*passwords/(local|compose|gcp)/{{client}}\.[A-Za-z0-9]+$
regex:^restart: (compose app ready|nothing needed;)
```

On GCP the helper uses the operator's Google login to read the current secret, run the passwords job, add the merged hashes to that same secret, save plain passwords in a local 0600 file, and delete both store objects including old versions. No Terraform change, new secret or job grant is needed. The service reads the latest secret within a minute through its existing accessor and the stored secret pointer. `kge passwords REVISION=yes` forces a revision when needed. Never print or share the whole plain file.

Compose keeps the merged hashes in the store; the playbook runs `kge restart` to load them. Each run prints its private file path; `passwords/<target>/<client>-latest` in the helper state directory points to the latest delivery file, which contains only newly issued passwords. The install's direct download fallback is `curl -o passwords.txt "http://127.0.0.1:${GCS_PORT}/storage/v1/b/kge-store/o/secrets%2F{{client}}%2Fpasswords.txt?alt=media"`, then restrict the file to 0600 and delete the plain store object. `docker compose down -v` removes local stack volumes. Locally the helper copies the private output into its state directory.

## 5. Upload the next complete inputs [mutating]

Page: the engineer can create a source, upload rows and draft a unit in Onboard. A complete pack upload is the operator's command. Prepare a folder containing `pack.yaml`, its declared includes, optional `catalogue.yaml` and the declared `fixture/`. After a test drive, use the installed client’s saved branded pack and declared fixture files: run `kge playbook operator UPLOAD=/path/to/inputs` with that complete folder. The shipped default is only for the unmodified Meridian example baseline; it does not reset or replace a branded test-drive baseline. The helper creates a fresh upload prefix and runs `onboard FROM=`; omitted people-owned inputs are removed, while job/page files remain. There is no people file in an upload. To change a data asset card in the portal, edit its entry in `catalogue.yaml`. `kge onboard` is today's full graph and catalogue run: one steward sign-off, then release; the catalogue publishes with that release and a failed gate publishes nothing. `kge onboard portal PRINCIPAL=<steward or access admin>` publishes descriptive fields and portal-only cards only, after one approval, and the portal shows them at its next catalogue refresh; it does not build a graph, run the gate or restart KGE. Omit `PRINCIPAL`, or set `PREVIEW=yes`, to print the card diff and write nothing. A steward is a certification role other than the pack content role `admin`. An access admin is reserved `access_admin` membership or `ACCESS_ADMINS`. Snapshots are kept at `clients/<client>/catalogue-publish/`. `kge onboard graph` is the graph checks, sign-off and release, and leaves descriptive fields as last published. `kge rollback portal PRINCIPAL=<steward or access admin>` restores the previous catalogue and does not move the graph. `kge served` prints the served graph hash and each card's state: In sync, Portal ahead, or Not in the knowledge graph. A portal-only card is visible only to people who may read its unit, and Ask and Search do not answer from it. The default upload below is still the full onboard. The portal shows a released catalogue change at its next five-minute check or restart. A portal admin's edit to a card field stays until `catalogue.yaml` changes that field again, and a table whose product leaves the release is hidden from the portal, not deleted.

```kge
kge upload {{upload}}
```
```expect
regex:^onboard: (candidate [0-9a-f]{12,64}|current already [0-9a-f]{12,64}|checked [0-9a-f]{12,64})
```

For the shipped example on a fresh Compose store use `kge setup-client CLIENT={{client}} ASKER=analyst_enterprise BASELINE=baselines/issue-316.json`, then `kge put-example` before onboarding, as INSTALL.md steps 4 and 5 show. On GCP follow INSTALL.md's baseline instructions for BigQuery. Locally the sample is already installed; do not run put-example. `setup-client` registers the client, asker role and baseline, never people. On a live client preserve its asker and baseline. Roles belong to the pack; people belong to the access table; changing a person moves no model hash. The gate asks as roles without reading people or passwords.

## 6. Preview teardown [read-only]

Targets: compose, gcp

Page: no teardown control. Command line: inspect the complete inventory before deciding to remove anything. The preview writes nothing. This store teardown is available on Compose and GCP; the local demo has no store teardown and skips this step.

```kge
kge teardown CONFIRM={{client}} PREVIEW=yes
```
```expect
regex:^teardown: clients/{{client}}/: [0-9]+ object\(s\)$
regex:^teardown: landing: [0-9]+ object\(s\)$
regex:^teardown: BigQuery: [0-9]+ object\(s\)$
regex:^teardown: preview only: nothing removed$
```

The destructive variant is **mutating**: `kge teardown CONFIRM={{client}}`. Add `PEOPLE=remove` before a fresh test drive to remove the profile entry too. Teardown backs up store data under `backups/{{client}}/`, but does not copy BigQuery tables or landing data. It keeps datasets, portal tables, access tables, Secret Manager and run records. Check that LANDING_URL is this client's own bucket/prefix. A repeat succeeds with nothing left. Separate temporary objects expire by lifecycle; deleted versioned objects remain noncurrent until retention removes them. With nothing released the service says to release first.

## 7. Inspect releases before an upgrade [read-only]

Page: Onboard, Versions shows history from the served bundle. After rollback that history is restored too and can show only the earlier release. Command line: on Compose/GCP, releases inventories bundles still held in the store, including the other retained release; use that list when planning a restore. Local releases reads the page history. Keep the last two identities and their stored inputs before replacing the image.

```kge
kge releases
```
```expect
regex:(^  [0-9a-f]{64}(?:  current)?$|^\{"profile":[^\n]*"versions":\s*\[\s*\{[^\n]*"model_hash":\s*"[0-9a-f]{64}")
```

Upgrade order (**mutating**): install the new image; run `kge access MIGRATE=yes BY=access_admin.{{client}}@example.com` **first**, before any loader can seed an empty table. Supply `EMAILS=old-id=role.{{client}}@example.com` mappings as needed; migration reports rows written and people still missing emails. Existing ids work until mapped. Preserve the migration map for portal history. Then onboard, sign off and release the new model. Before the first release, grants and review actions use the current pack's roles. After release they use served pack roles. Finally grant/revoke people through the access admin and run passwords. Access changes themselves need no release or revision; rollback leaves access as the live table says.

Older releases are restorable only as published; previous same-hash releases without saved identities refuse. Only the two retained snapshots can be restored. New rollback restores saved inputs and fixture bytes; an older snapshot without fixture files reports the digest for re-upload. A failed onboard restores its inputs and sweeps its own new objects; rerun after fixing the named finding. A killed job's lease must expire before retrying. Do not add retry loops.

For installation checks, follow INSTALL.md and review each applicable step in this playbook. Context’s Docs contains the end-user page instructions for preparing sources, business acceptance, steward certification, checks, sign-off, release and rollback. Those actions keep their separate role permissions.

### Cloud Run invokers for an external agent

With `public = false`, Cloud Run admits a caller only when that caller holds `roles/run.invoker` on `kge-<client>`. Name each caller in Terraform `invokers`. INSTALL.md, "Cloud Run invokers for an external agent", is the apply procedure. Replace every placeholder with the installation's own value, and do not write a project id, a project number, an organisation id, an agent id or a service URL into this guide.

An Agent Studio agent (`aiplatform.googleapis.com`) calls as a principal identity:

`principal://agents.global.org-<ORG_ID>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUMBER>/locations/global/agents/<AGENT_ID>`

A set of such identities is a `principalSet://` member with the same kind of path. A Conversational Agents (Dialogflow) OpenAPI tool calls as the Dialogflow service agent:

`serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-dialogflow.iam.gserviceaccount.com`

Enabling an API does not create its service agent. Create the identity before the grant, or the binding has no member to attach. For Conversational Agents the API is `dialogflow.googleapis.com`. The same command applies to any newly enabled API whose service agent must hold `roles/run.invoker`:

```sh
gcloud beta services identity create --service=dialogflow.googleapis.com
```

`allUsers` and `allAuthenticatedUsers` are never invokers. Studio's Google identity token travels in `Authorization`. The KGE token travels in `X-KGE-Authorization`. The kit's reference OpenAPI tool spec, `openapi/kge-tools.openapi.yaml`, declares that header on `/context`, `/ask`, `/search` and `/data/query`.
