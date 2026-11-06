# Steward

Page: open `/context` at the portal’s address, sign in with your email and password, and choose Context from the app switcher. Docs opens Context’s end-user guides. The standalone service page is off by default. An operator can set `CONTEXT_PAGE=on` and restart the service to restore it as a fallback at the service’s `/`; use the same credentials, then return the setting to `off` when the portal is available. APIs and MCP remain available in either mode.


Sign in as steward_glossary.{{client}}@example.com. The engineer and business playbooks prepare and accept the kit’s leasing unit (UNIT=leasing by default). A different business person must have accepted its items. The candidate must be checked after certification before sign-off; ask the engineer to run checks when new content needs building.

These are the dev team’s executable playbooks, kept only in the repository. Context’s Docs contains separate end-user page guides. Run `kge playbook steward` in the selected environment; read the whole guide first. This scratch example uses the selected client’s test-drive people. The runner substitutes `{{client}}` in commands and expected output; when following the guide by hand, replace it with your installed client id. For an existing installation with different people, use a working copy with those usernames.

Every numbered step has a page way and a `kge` way. `[mutating]` changes application or local credential state; `--safe` skips it and requires an already connected environment; it never signs in or starts a proxy. `--dry-run` prints commands without executing any command or checking output. A `capture:name=pattern` expectation saves the first matched group for later `{{name}}` arguments; `different:first,second` checks that two captured values differ; `equal:first,second` checks that they match. `json:path=value` checks a field in the last JSON response against a JSON value; a quoted `{{name}}` uses a saved capture. Values substituted into `regex:` expectations are matched literally. Each other `expect` line is a literal substring of that step's combined stdout, except `regex:` lines, which use Python regular expressions with multiline matching. A nonzero command or missing expectation stops at the named step. Arguments are passed directly, without a shell. `{{env}}`, `{{client}}` and `{{folder}}` come from the selected environment; the scratch run defaults to UNIT=leasing and the shipped Meridian upload. `{{examples}}` locates the shipped examples. Override `UNIT=`, `PROPOSAL=` or `UPLOAD=` for your own work. A broken step fails acceptance. `--from-step N --through-step N` runs a numbered range for a human handoff; omitted bounds mean the first or last step. A `Targets:` line limits a step to the named targets; other targets print a skip. `{{decision}}` uses PROPOSAL= when supplied, otherwise UNIT=; read and choose that review target before execution.

Before the first connection, the operator registers the environment: `kge add local` from the local demo folder, `kge add compose /path/to/compose`, or `kge sync` on GCP. Select it with `kge use local/{{client}}`, `kge use compose/{{client}}` or `kge use gcp/{{client}}`. GCP discovery is read-only; `kge setup` also prompts for missing settings. `kge config` shows the saved settings; state holds no secrets.

## 1. Connect [mutating]

Page: sign in with your email and password. The Docs section opens the guides for your live access-table roles. Command line: `kge connect <email>` switches to that person if needed and prompts privately for their password. It starts `gcloud run services proxy` on GCP, then signs in if there is no valid token. Your Google account needs `roles/run.invoker`. Local and Compose targets use their local URL without a proxy. `kge proxy`, `kge login`, `kge logout` and `kge proxy stop` are separate controls; `kge disconnect` stops the proxy and forgets the token. API commands auto-connect; `kge config AUTO=off` disables that.

```kge
kge connect steward_glossary.{{client}}@example.com
```
```expect
regex:^connected: (local|compose|gcp)/{{client}} as steward_glossary\.{{client}}@example\.com \((?!None\b|null\b)[a-z][a-z0-9_-]*(?:, (?!None\b|null\b)[a-z][a-z0-9_-]*)*\)$
```

## 2. Review and certify [mutating]

Page: Onboard, review the accepted items and choose Certify all only after reviewing every accepted item. Read industry mappings, restrictions and held-out results; do not certify your own acceptance. Command line: review, then certify the accepted unit. Use `kge certify PROPOSAL=<proposal id>` for an accepted improvement instead.

```kge
kge served
kge review
kge certify {{decision}}
```
```expect
capture:before=^served: ([0-9a-f]{64})$
regex:"changed":\s*\[\s*"[^"\s]+"
regex:"said":\s*"certified"
```

## 3. Check certified inputs and read eval pre [mutating]

Page: the engineer signs in and chooses Run checks after certification, then the steward signs back in to review the candidate gate, changes and eval pre report. Command line: the playbook makes that same handoff explicitly. Enter the engineer's password, then the steward's, at the two private prompts. The engineer runs the checks with their own token; steward permissions are unchanged. There is no out-of-playbook preparation step.

```kge
kge connect knowledge_engineer.{{client}}@example.com
kge checks
kge wait
kge connect steward_glossary.{{client}}@example.com
kge review
```
```expect
regex:^job: done \([^)]+\)$
json:candidate.gate.passed=true
json:eval_pre.passed=true
capture:candidate="candidate":\s*\{\s*"hash":\s*"([0-9a-f]{64})"
different:before,candidate
```

The checks run eval pre; `kge eval pre` can also refresh its report. Never sign off a missing or failed gate. This scratch rehearsal expects leasing to produce a different hash from the installed release.

## 4. Sign off [mutating]

Page: choose Sign off only after the candidate checks pass and its changes are understood. Command line: the API checks your live table roles against the candidate's roles. It takes your identity from your bearer token, never a body field.

```kge
kge sign-off
kge wait
kge review
```
```expect
regex:"job":\s*\{[^\n]*"step":\s*"sign-off"[^\n]*"state":\s*"done"
regex:^job: done \([^)]+\)$
```

## 5. Release [mutating]

Page: choose Release. Command line: `publish` is the governed API release; `release` is the operator's job command. The release runs eval post automatically against the served graph and compares it with that candidate's eval pre results. There is no manual post command or button. Everyone with release access can read its report. The post result shown is the latest check of a release, including a check after rollback. A deterministic or must-pass difference fails loudly; model-graded score differences and their 95% interval are informational. The baseline is re-recorded only after this passes.

```kge
kge publish
kge wait
kge served HASH={{candidate}}
kge versions
```
```expect
capture:published=^served: ([0-9a-f]{64})$
equal:candidate,published
different:before,published
json:eval_post.passed=true
json:eval_post.identity.hash="{{published}}"
regex:^job: done \([^)]+\)$
```

On failure the job and page say **run kge rollback**. No automatic rollback occurs. Investigate the report and ask the operator to run that recovery, or use the governed rollback below.

## 6. Rehearse rollback [mutating]

Page: Onboard, choose Roll back to the previous release. Command line: `unpublish` is the governed API rollback; `kge rollback` is the operator's equivalent. Only run this rehearsal in a scratch environment with two retained releases. Rollback restores the prior release's saved inputs and warehouse identity byte for byte and leaves live access unchanged. It runs eval post automatically too; read that report before resuming use. Both steps read `kge versions` and require eval post to have passed for the hash actually served.

```kge
kge unpublish
kge wait
kge served HASH={{before}}
kge versions
```
```expect
capture:restored=^served: ([0-9a-f]{64})$
equal:before,restored
different:published,restored
json:eval_post.passed=true
json:eval_post.identity.hash="{{restored}}"
regex:^job: done \([^)]+\)$
```

Compose and GCP services follow both transitions on the status poll; local page jobs switch the serving process when they finish. The playbook waits for the job, then checks the hash actually served by the API. It must switch to the checked candidate after publish and back to the captured original after unpublish. A partial run starting after step 2 has no captured original: use the whole guide for this rollback rehearsal.
