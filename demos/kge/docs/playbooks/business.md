# Business team

Page: open `/context` at the portal’s address, sign in with your email and password, and choose Context from the app switcher. Docs opens Context’s end-user guides. The standalone service page is off by default. An operator can set `CONTEXT_PAGE=on` and restart the service to restore it as a fallback at the service’s `/`; use the same credentials, then return the setting to `off` when the portal is available. APIs and MCP remain available in either mode.


Sign in as business_team.{{client}}@example.com. The preceding knowledge-engineer playbook prepares examples/leasing (UNIT=leasing by default). The example accepts all reviewed items of that unit; do not run it until you have read them.

These are the dev team’s executable playbooks, kept only in the repository. Context’s Docs contains separate end-user page guides. Run `kge playbook business` in the selected environment; read the whole guide first. This scratch example uses the selected client’s test-drive people. The runner substitutes `{{client}}` in commands and expected output; when following the guide by hand, replace it with your installed client id. For an existing installation with different people, use a working copy with those usernames.

Every numbered step has a page way and a `kge` way. `[mutating]` changes application or local credential state; `--safe` skips it and requires an already connected environment; it never signs in or starts a proxy. `--dry-run` prints commands without executing any command or checking output. A `capture:name=pattern` expectation saves the first matched group for later `{{name}}` arguments; `different:first,second` checks that two captured values differ. Values substituted into `regex:` expectations are matched literally. Each other `expect` line is a literal substring of that step's combined stdout, except `regex:` lines, which use Python regular expressions with multiline matching. A nonzero command or missing expectation stops at the named step. Arguments are passed directly, without a shell. `{{env}}`, `{{client}}` and `{{folder}}` come from the selected environment; the scratch run defaults to UNIT=leasing and the shipped Meridian upload. `{{examples}}` locates the shipped examples. Override `UNIT=`, `PROPOSAL=` or `UPLOAD=` for your own work. A broken step fails acceptance. `--from-step N --through-step N` runs a numbered range for a human handoff; omitted bounds mean the first or last step. A `Targets:` line limits a step to the named targets; other targets print a skip. `{{decision}}` uses PROPOSAL= when supplied, otherwise UNIT=; read and choose that review target before execution.

Before the first connection, the operator registers the environment: `kge add local` from the local demo folder, `kge add compose /path/to/compose`, or `kge sync` on GCP. Select it with `kge use local/{{client}}`, `kge use compose/{{client}}` or `kge use gcp/{{client}}`. GCP discovery is read-only; `kge setup` also prompts for missing settings. `kge config` shows the saved settings; state holds no secrets.

## 1. Connect [mutating]

Page: sign in with your email and password. The Docs section opens the guides for your live access-table roles. Command line: `kge connect <email>` switches to that person if needed and prompts privately for their password. It starts `gcloud run services proxy` on GCP, then signs in if there is no valid token. Your Google account needs `roles/run.invoker`. Local and Compose targets use their local URL without a proxy. `kge proxy`, `kge login`, `kge logout` and `kge proxy stop` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect; `kge config AUTO=off` disables that.

```kge
kge connect business_team.{{client}}@example.com
```
```expect
regex:^connected: (local|compose|gcp)/{{client}} as business_team\.{{client}}@example\.com \((?!None\b|null\b)[a-z][a-z0-9_-]*(?:, (?!None\b|null\b)[a-z][a-z0-9_-]*)*\)$
```

## 2. Review the draft [read-only]

Page: Onboard, inspect the unit's graph views: Physical, Glossary, Metrics, Industry ontology, Documents and Governance. Read each definition, figure, proposed restriction and proposed removal. Confirm meaning with the source owner. Command line: inspect the same items and progress.

```kge
kge review
```
```expect
regex:"units":\s*\[\s*\{
regex:"state":\s*"drafted"
```

## 3. Accept the reviewed items [mutating]

Page: choose Accept all only after checking every item in the unit. Command line: accept the chosen unit; use `ITEMS=<item id>` for a single item. For an improvement, use `kge accept PROPOSAL=<proposal id>` instead. Your email is audited in history, while only your acting role enters the compiled content. Removing your access later does not erase that audit or block onboarding. A different steward must certify your acceptance.

```kge
kge accept {{decision}}
```
```expect
regex:"changed":\s*\[\s*"[^"\s]+"
regex:"said":\s*"accepted"
```
