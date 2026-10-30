# How onboarding works

The onboarding page keeps each business unit's one-line status, open requests and history together. It also shows the documents supplied for that unit and how each one was certified or graded, and the unit's progress in each of the six parts of its graph: physical tables, glossary, metrics, industry ontology, documents, and governance. For each part it counts the items drafted, accepted by your business team and certified by your data steward.

The stepper on each unit's card shows the stage the unit has reached, and the next step's button is highlighted. The stages, in order, are the sections below.

## Before you start

The client pack defines roles and their readable units. Your platform team assigns people to those role ids with `setup-client`; the platform defines the processes a role may perform. The automated processes are `drafting`, `building` and `improving`. Business acceptance uses `accepting` (older business roles may instead write terms and mappings without certification). Stewardship uses `certification`; asking questions uses `querying`. A person's display name does not grant a role.

| Person | Work |
|---|---|
| Knowledge engineer | Upload sources and rows, draft, run checks, propose improvements |
| Business team | Read drafts, accept them, answer open requests |
| Data steward | Certify, sign off after checks pass, release |
| End user | Ask and discover within the units their role may read |
| Platform engineer | Install and configure the service; no KGE role by default |

On the Meridian sample, sign in as `knowledge_engineer` to upload and draft, `business_team` to accept, and `steward_glossary` to certify. Return to `knowledge_engineer` for **Run checks**, then to `steward_glossary` for **Sign off** and **Release**. Use each person's assigned password and sign out before changing people. A hidden or refused control means your role cannot perform that step. Under the local install, a queued check or release displays a command for the job operator to run; wait for it to finish before continuing.

Whoever runs the KGE job is a knowledge engineer, including a person executing the job through Cloud Run or Docker Compose. That operator has break-glass sign-off and release power by naming a steward; Cloud Run records it in its execution log. The page remains the governed route, with separate engineer and steward identities.

To add an engineer to an older installation, the platform team first adds the role's processes to the pack and the person's binding through `setup-client`, retaining all existing people, the asker and the baseline. The engineer builds and checks a new candidate, and the steward signs off and releases it. If onboarding reports `current already`, the operator must `reload` before the check and sign-off: a skipped load does not bind newly assigned people. Only then generate passwords: they come from the released artifact, so assigning the person alone does not create a login. This rotates existing passwords too. Update the service's password hashes, restart it and hand out the new passwords privately. The install guide gives the commands.

## Manual and assisted routes

The page and the install guide's manual commands remain available. The kit also includes four knowledge-engineer skills under `skills/`, each in its own folder with a `SKILL.md`. Load the chosen skill into an assistant connected to KGE over HTTP or MCP, authenticated as the person's own principal. Each skill checks the unit's state first and follows the same steps on either transport.

Use **land-and-draft** to preview and upload sources and rows and draft items. The business team then accepts and the steward certifies on the page. Use **build-and-gate** to run checks and read the candidate's margin report. Sign-off and release remain the steward's page actions; the skills never take human decisions or invoke the job operator's break-glass powers.

After release, **improve** is a separate proposal workflow, followed by business and steward review and fresh checks. Runs are client-wide by default. Select one unit with `UNIT=<id>` on the manual or image command, or `unit` on HTTP/MCP. Evidence and proposed edits are limited to that unit. An unknown unit is refused, and so is one outside the person's improving grant and readable units: on HTTP and MCP always, on the manual or image command when it names `PRINCIPAL=<person>`. Candidate regression checks still protect the whole client. **ask** tests golden questions through each person's own connection, compares returned answers before and after a pack change is served, and explains misses. It never computes or alters a figure and treats refusals and abstentions as answers.

## 1. Uploaded — your knowledge engineer

Send a description of your data in any form you have: a semantic model such as OSI, dbt or LookML, a data dictionary, a Markdown document, or a database schema. Send the data as one CSV file per table. Reference documents are optional. If your Microsoft 365 process certified a document, we keep that certification; otherwise we check and grade it here.

On the page, choosing a schema file previews its tables and **Create source** uploads it. This also covers a source created from one of the other supported descriptions. **Add rows** adds the unit's rows, as CSV files or invented from the description, when a request asks for them.

## 2. Drafted — your knowledge engineer

Your knowledge engineer uses **Draft** to prepare proposed terms, definitions, calculations, and test questions with expected answers. Nothing is live. The unit card lists open requests for anything missing.

**Draft** also links your terms to industry standards. We hold a fixed, versioned set of reference concepts from published standards, such as FIBO for financial terms, the Data Privacy Vocabulary for personal data, and Dublin Core, and map your terms only to those: nothing is fetched while drafting. Each proposed link names the standard's concept and how it relates to your term: close, broader, narrower or related. An exact match is only ever your data steward's call. The link also names the standard and its version. Where no concept fits a term, the unit card asks your data steward to confirm that there is no industry equivalent, or to name the nearest.

**Draft** also proposes data policies. A column whose name, description or values look like personal data, such as a name, an email address or a date of birth, is proposed as restricted, linked to the matching personal-data concept. Once certified, no question can read that column.

## 3. Accepted — your business team

Your business team reviews the items and uses **Accept all** to accept them, including each proposed link to an industry standard and each proposed data policy. It also answers the open requests, asking the knowledge engineer to upload revised materials. The business team can read and accept; upload and draft controls belong to the knowledge engineer.

## 4. Certified — your data steward

Your data steward uses **Certify all** after the business review. The Documents list says whether each reference document kept Microsoft 365 certification, was graded here, or was not used, and shows its tier where one was assigned.

## 5. Checks — your knowledge engineer

**Run checks** confirms that the candidate answers your test questions correctly and does at least as well as the previous version. The candidate does not go live when these checks run.

## 6. Live — your data steward

Your data steward uses **Sign off**, then **Release**; the candidate becomes live. The previous version stays available for rollback. Once a unit is live, the knowledge engineer uses **Improve: Run** to propose changes from the questions it misses, for your teams to review as in stages 3 and 4.

## 7. Keep it current — your knowledge engineer, business team and data steward

A live unit stays current by sending its sources again: changed data, a changed description of your data, changed documents, or your own figures. Choose **Update this unit** under More actions on its card, then send them the same way as the first time, with **Create source**; the whole set, not only what changed. Nothing live changes while the new version is reviewed.

**Draft** then compares the new version with the live one, item by item, and the unit card shows what changed. Its status line reads, for example, "v2 (update): 3 changed, 1 added, 1 removed — awaiting acceptance", and **What changed** lists each item with what changed in it: a table or column, a definition, a calculation and its conditions, an alias, a document, or a test question's expected answer.

- Items that did not change keep their acceptance and certification.
- New and changed items are reviewed as in stages 3 and 4.
- Anything the new version no longer has is a proposed removal. It is accepted and certified like any other item; nothing is removed silently.
- New rows alone recalculate the test questions' expected answers. A question whose answer changes is listed as changed, so your data steward sees the new figure before it is used.
- If a removal would break a released test question, or a question of another unit, it is an open request until it is resolved.

When every changed item is certified, the new version goes through the same automatic checks, sign-off and release as in stages 5 and 6. The new version's data rows are kept apart from the live version's. The automatic checks run on the new rows, while the previous version keeps answering with its own definitions and its own rows until the new one is released. After the release, the previous version stays available for rollback, and a rollback restores both its definitions and its rows. The unit's history records each update and what it changed.

## Discover the released result

The KGE page serves operators and people asking governed questions. The discovery portal can run beside it or alone. In the paired Meridian sample, sign in to the portal as `analyst_retail`: browse the ten Retail tables, search for **Account**, and open it. **Data preview** shows the allowed rows and columns; **Export CSV** downloads a capped export. In **Run a query**, use the suggested SQL or `SELECT account.account_id FROM account LIMIT 5`. Only readable tables and allowed columns are available. Ask *Total retail revenue for June 2025* to get **13,287.67** with its certified sources, then sign out.

Onboarding builds a catalogue from the tables and columns in the candidate. After release the portal syncs it at startup and every five minutes. Portal descriptions, favourites, ratings, requests and feedback remain in the portal's own tables; a sync preserves them. KGE supplies the released structural and governance metadata. Editing a portal description is visible immediately; KGE Search and Ask use the released description until the change passes acceptance, certification, checks, sign-off and release.

A catalogue entry or badge alone does not authorise rows. The table must have landed in the served release, and each read applies the person's units, allowed columns and row selectors in their context. For Meridian, the Wealth context's `banks_with_retail` selector does not filter a Retail analyst's direct read of `retail.customer`. If a release predates the landed-table record, ask the job operator to follow the install guide's upgrade steps: `reload` when onboarding skips an unchanged hash, then checks, new sign-off, release and restart.

With KGE, Search finds certified terms, synonyms, related products and governed document passages. The portal's own Search and Ask use its catalogue tables: Ask can describe owners and fields but cannot answer a financial figure. Those providers are also available when the portal runs alone; preview, export and query require KGE. A notice says when an unavailable KGE has caused a fallback to the portal catalogue. An access refusal does not cause fallback.

## Try a fresh installation

The install guide's `test-drive CONFIRM=test-drive` uses the installed client's id and the invented Meridian sample. Optional `NAME` chooses its display name; without it, Meridian remains Meridian Union Bank. The test drive checks the installation, creates the branded pack, seeds and locks its catalogue, builds, checks and releases, then tests the released answers and data reads. Keep this pack and release as an editable starting baseline. It refuses an installation that already has client data. The manual equivalent starts with `put-baseline`, then `seed`, `lock`, `onboard`, sign-off and release. Later estate changes need a fresh lock before onboarding. Passwords are generated after release.

## Give an agent access

Use your own assigned identity. With password sign-in enabled, send `POST /session/token` to the KGE service over HTTPS with JSON `{"username":"<username>","password":"<password>"}` and `Content-Type: application/json`. The response supplies an `access_token`, `token_type` of `Bearer` and `expires_in` of 900 seconds. Give only that token to the agent, as `Authorization: Bearer <token>` on each REST or MCP request; browser cookies do not sign in to MCP. On a private Cloud Run service, its Google identity token is also required in `X-Serverless-Authorization`.

The MCP servers are `/mcp` for context, `/mcp/answer` for Ask, `/mcp/identity` for readable units, `/mcp/catalogue` for the catalogue, `/mcp/data` for data reads, `/mcp/search` for search and `/mcp/onboard` for automated onboarding. They retain your role and readable units. Human acceptance, certification, sign-off and release have no MCP tools. Obtain a new token after 15 minutes; browser sign-out does not revoke it. Keep credentials and tokens out of shared files and logs. Failed credentials return 401; throttled attempts return 429 and a retry delay.
