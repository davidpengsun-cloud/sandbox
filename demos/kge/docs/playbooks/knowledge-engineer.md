# Knowledge engineer

Page: open `/context` at the portal’s address, sign in with your email and password, and choose Context from the app switcher. Docs opens Context’s end-user guides. The standalone service page is off by default. An operator can set `CONTEXT_PAGE=on` and restart the service to restore it as a fallback at the service’s `/`; use the same credentials, then return the setting to `off` when the portal is available. APIs and MCP remain available in either mode.


Sign in as knowledge_engineer.{{client}}@example.com. This playbook prepares the kit’s leasing source and draft. Pass UNIT=<unit id> only for a matching source of your own.

These are the dev team’s executable playbooks, kept only in the repository. Context’s Docs contains separate end-user page guides. Run `kge playbook knowledge-engineer` in the selected environment; read the whole guide first. This scratch example uses the selected client’s test-drive people. The runner substitutes `{{client}}` in commands and expected output; when following the guide by hand, replace it with your installed client id. For an existing installation with different people, use a working copy with those usernames.

Every numbered step has a page way and a `kge` way. `[mutating]` changes application or local credential state; `--safe` skips it and requires an already connected environment; it never signs in or starts a proxy. `--dry-run` prints commands without executing any command or checking output. A `capture:name=pattern` expectation saves the first matched group for later `{{name}}` arguments; `different:first,second` checks that two captured values differ. Values substituted into `regex:` expectations are matched literally. Each other `expect` line is a literal substring of that step's combined stdout, except `regex:` lines, which use Python regular expressions with multiline matching. A nonzero command or missing expectation stops at the named step. Arguments are passed directly, without a shell. `{{env}}`, `{{client}}` and `{{folder}}` come from the selected environment; the scratch run defaults to UNIT=leasing and the shipped Meridian upload. `{{examples}}` locates the shipped examples. Override `UNIT=`, `PROPOSAL=` or `UPLOAD=` for your own work. A broken step fails acceptance. `--from-step N --through-step N` runs a numbered range for a human handoff; omitted bounds mean the first or last step. A `Targets:` line limits a step to the named targets; other targets print a skip. `{{decision}}` uses PROPOSAL= when supplied, otherwise UNIT=; read and choose that review target before execution.

Before the first connection, the operator registers the environment: `kge add local` from the local demo folder, `kge add compose /path/to/compose`, or `kge sync` on GCP. Select it with `kge use local/{{client}}`, `kge use compose/{{client}}` or `kge use gcp/{{client}}`. GCP discovery is read-only; `kge setup` also prompts for missing settings. `kge config` shows the saved settings; state holds no secrets.

## 1. Connect [mutating]

Page: sign in with your email and password. The Docs section opens the guides for your live access-table roles. Command line: `kge connect <email>` switches to that person if needed and prompts privately for their password. It starts `gcloud run services proxy` on GCP, then signs in if there is no valid token. Your Google account needs `roles/run.invoker`. Local and Compose targets use their local URL without a proxy. `kge proxy`, `kge login`, `kge logout` and `kge proxy stop` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect; `kge config AUTO=off` disables that.

```kge
kge connect knowledge_engineer.{{client}}@example.com
```
```expect
regex:^connected: (local|compose|gcp)/{{client}} as knowledge_engineer\.{{client}}@example\.com \((?!None\b|null\b)[a-z][a-z0-9_-]*(?:, (?!None\b|null\b)[a-z][a-z0-9_-]*)*\)$
```

## 2. Create the sample source [mutating]

Page: Onboard, Create source. Use unit id leasing, label Asset Finance, upload examples/leasing/schema.sql, choose invented rows, and upload examples/leasing/questions.json and examples/leasing/heldout.json. These files ship in the kit. Command line: send those same files to the source API as the signed-in engineer. No principal is supplied in the body.

```kge
kge source UNIT={{unit}} LABEL="Asset Finance" DDL={{examples}}/leasing/schema.sql ROWS=invented QUESTIONS={{examples}}/leasing/questions.json HELDOUT={{examples}}/leasing/heldout.json
```
```expect
regex:"unit":\s*"leasing"
regex:"tables":\s*\[\s*\{
regex:"rows":\s*[1-9][0-9]*
```

## 3. Draft and inspect the work [mutating]

Page: choose Draft, then review the six parts of its graph, industry standards and data policies. Read What changed and every proposed removal. Business accepts; a different steward certifies. Command line: prepare and inspect the same draft. The business and steward playbooks use this leasing unit by default; no separate preparation is needed.

```kge
kge draft UNIT={{unit}}
kge review
```
```expect
regex:"items":\s*\[\s*\{
regex:"state":\s*"drafted"
regex:"units":\s*\[\s*\{
```

Checks run after certification, during the steward playbook's explicit engineer sign-in. They include eval pre and the up-front findings. A changed fixture must remain a fixture; missing declared files refuse early.

For a separate improvement workflow, choose Improve on the page or run `kge improve UNIT=retail`, then `kge wait` and `kge review`. The rules path may report **no proposal kept**; that is not a proposal ready for acceptance. Continue only when review contains a proposal object with a nonempty id and items. Do not use a text match on the word proposal: `"proposal":null` is no work to review. Business accepts a kept proposal, a different steward certifies it, and the engineer runs fresh checks. No improvement releases itself. This scratch source-onboarding playbook does not require the model to discover an improvement.

The baseline is re-recorded only after a release's automatic post check passes. A changed question set declares `changes_from`, previous version/digest, changed/removed ids and why; the comparison cannot silently drop a set. Older baselines compare pass rate until their first release-time re-record.
