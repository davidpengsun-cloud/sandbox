# Steward

You review accepted work, certify it, sign off a checked candidate, publish a graph version and restore the previous release when needed. You cannot certify your own acceptance. Your assigned permissions determine the actions available to you.

## 1. Review and certify

Sign in, choose Context from the app switcher, and open Onboard. Select the accepted business area or improvement proposal. Read its terms, mappings, ontology, industry standards, data policies, documents, restrictions and proposed removals. Choose Certify all only for work accepted by a different person and ready for checks.

## 2. Review the checked candidate

Ask a knowledge engineer with permission to choose Run checks after certification. When the job finishes, review the candidate gate, What changed and eval pre report. Do not sign off a missing or failed gate. Changes to the candidate require fresh checks and sign-off.

## 3. Sign off

Choose Sign off after the checks pass and you understand the candidate's changes. Wait for the job to finish and read its result before continuing.

## 4. Release

Choose Release and wait for completion. The release automatically runs eval post against the graph now served. Read the report in Versions: it is the latest check of a release, including a check after rollback. A failed post check needs investigation and recovery; it does not automatically restore the previous version.

## 5. Restore the previous release

Open Versions and review the current and previous releases. When recovery is needed, choose Roll back to the previous release. This restores the prior release's saved inputs and warehouse identity and leaves live access unchanged. Wait for completion and read the automatic eval post report before resuming use.
