# Access admin

Page: open `/context` at the portal’s address, sign in with your email and password, and choose Context from the app switcher. Docs opens Context’s end-user guides. The standalone service page is off by default. An operator can set `CONTEXT_PAGE=on` and restart the service to restore it as a fallback at the service’s `/`; use the same credentials, then return the setting to `off` when the portal is available. APIs and MCP remain available in either mode.


Sign in as access_admin.{{client}}@example.com in the scratch example. This guide is for reserved access_admin table rows or ACCESS_ADMINS members. The API cannot create an access admin.

These are the dev team’s executable playbooks, kept only in the repository. Context’s Docs contains separate end-user page guides. Run `kge playbook access-admin` in the selected environment; read the whole guide first. This scratch example uses the selected client’s test-drive people. The runner substitutes `{{client}}` in commands and expected output; when following the guide by hand, replace it with your installed client id. For an existing installation with different people, use a working copy with those usernames.

Every numbered step has a page way and a `kge` way. `[mutating]` changes application or local credential state; `--safe` skips it and requires an already connected environment; it never signs in or starts a proxy. `--dry-run` prints commands without executing any command or checking output. A `capture:name=pattern` expectation saves the first matched group for later `{{name}}` arguments; `different:first,second` checks that two captured values differ. Values substituted into `regex:` expectations are matched literally. Each other `expect` line is a literal substring of that step's combined stdout, except `regex:` lines, which use Python regular expressions with multiline matching. A nonzero command or missing expectation stops at the named step. Arguments are passed directly, without a shell. `{{env}}`, `{{client}}` and `{{folder}}` come from the selected environment; the scratch run defaults to UNIT=leasing and the shipped Meridian upload. `{{examples}}` locates the shipped examples. Override `UNIT=`, `PROPOSAL=` or `UPLOAD=` for your own work. A broken step fails acceptance. `--from-step N --through-step N` runs a numbered range for a human handoff; omitted bounds mean the first or last step. A `Targets:` line limits a step to the named targets; other targets print a skip. `{{decision}}` uses PROPOSAL= when supplied, otherwise UNIT=; read and choose that review target before execution.

Before the first connection, the operator registers the environment: `kge add local` from the local demo folder, `kge add compose /path/to/compose`, or `kge sync` on GCP. Select it with `kge use local/{{client}}`, `kge use compose/{{client}}` or `kge use gcp/{{client}}`. GCP discovery is read-only; `kge setup` also prompts for missing settings. `kge config` shows the saved settings; state holds no secrets.

## 1. Connect [mutating]

Page: sign in with your email and password. The Docs section opens the guides for your live access-table roles. Command line: `kge connect <email>` switches to that person if needed and prompts privately for their password. It starts `gcloud run services proxy` on GCP, then signs in if there is no valid token. Your Google account needs `roles/run.invoker`. Local and Compose targets use their local URL without a proxy. `kge proxy`, `kge login`, `kge logout` and `kge proxy stop` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect; `kge config AUTO=off` disables that.

```kge
kge connect access_admin.{{client}}@example.com
```
```expect
regex:^connected: (local|compose|gcp)/{{client}} as access_admin\.{{client}}@example\.com \((?!None\b|null\b)[a-z][a-z0-9_-]*(?:, (?!None\b|null\b)[a-z][a-z0-9_-]*)*\)$
```

## 2. List access and check for outside changes [read-only]

Page: there is no Context access-editor screen yet; use these commands beside the page. Ask the operator to run `kge admins access_admin.{{client}}@example.com BY=operator.{{client}}@example.com`, retaining the full admin list: this command replaces it. The scratch install and operator guide both use only access_admin.{{client}}@example.com. Inspect `changed_outside`: true reports a table edit outside Context, including deletion of all rows. Investigate it against the audit before another write.

```kge
kge list
kge audit
```
```expect
regex:"people":\s*\[\s*\{
regex:"changed_outside":\s*false
regex:"entries":\s*\[\s*\{
```

## 3. Grant a sample reader [mutating]

Page: use the command; the person's next page request follows the live table within a minute. Before the first release, grants use the current pack's roles; after release they use served pack roles. The operator runs passwords for a new person. Roles in a grant replace that person's ordinary role list, so retain existing roles. Unknown roles and `access_admin` are refused; the ordinary pack role `admin` is grantable. No release or model-hash change occurs.

```kge
kge grant analyst_retail.{{client}}@example.com ROLES=analyst_retail NAME="Retail analyst"
```
```expect
regex:"after":\s*\{[^\n]*"roles":\s*\["analyst_retail"\]
regex:"username":\s*"analyst_retail\.{{client}}@example\.com"
```

## 4. Revoke the sample reader and restore it [mutating]

Page: after revocation the reader cannot sign in and existing sessions/tokens stop at their next request within the table's one-minute TTL. This rehearsal restores the sample row for later acceptance. In a real removal stop after revoke.

The sign-in throttle (5 failures, 300 s lockout) can lock out a person who retries repeatedly in the minute after a regrant; wait for the refresh before retrying.

```kge
kge revoke analyst_retail.{{client}}@example.com
kge grant analyst_retail.{{client}}@example.com ROLES=analyst_retail NAME="Retail analyst"
kge audit
```
```expect
regex:"before":\s*\{
regex:"after":\s*\{[^\n]*"roles":\s*\["analyst_retail"\]
regex:"username":\s*"analyst_retail\.{{client}}@example\.com"
```

## 5. Check portal administration [read-only]

Page: open the portal's Admin access, User management. Context access admins are automatically portal admins, unioned with portal grants. Portal roles remain `viewer` (default), `admin`, `publisher` and `approver`; a portal admin can grant/remove them there, with an audit of who, when, before and after. Changes take effect at the next request; unknown roles and non-admin writes refuse. Viewers browse the catalogue and use the governed data they can read. Publishers publish products and edit catalogue metadata within their units. Approvers decide access requests; admins also manage portal roles. Nobody gets a role from their name. Shared sign-in uses the same email and display name; migration keeps favourites, cart, requests, decisions and feedback.

Command line: inspect the Context admin list here. Portal-specific role changes belong to its own page, not `kge grant`.

```kge
kge list
```
```expect
regex:"admins":\s*\["access_admin\.{{client}}@example\.com"\]
```
