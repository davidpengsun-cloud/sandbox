# Knowledge engineer

You draft a business area's terms and onboard its documents and data. The actions available to you follow your assigned permissions. Business teams confirm meaning; a different steward certifies accepted work and signs off the checked candidate. You release it and restore the previous release when needed; you never certify or sign off.

## 1. Open your work

Sign in, choose Context from the app switcher, and open Onboard. Choose the business area you are working on. Review its status and existing content before making changes.

## 2. Create a source

Open Add a unit. Enter Unit and Label, then choose the DDL file describing the tables. Add CSV files, one per table, and question sets if you have them. Choose the appropriate Rows option, review the preview, then choose Create source. Use Add rows when you need to upload rows for a source.

## 3. Draft and review

Choose Draft. Review the six parts of its graph: Physical, Glossary, Metrics, Industry ontology, Documents and Governance. Read What changed, including every proposed removal, and check definitions and restrictions against the source owner's requirements.

Before uploading documents, ensure they contain no personal or sensitive data; this is the client's responsibility. In What changed, review any document reported as content not read and the reason. Its title and metadata remain searchable, but its unread content cannot be cited as evidence. Certification attests the converted document.

Ask the business team to review and accept the items whose meaning they have confirmed. A different steward certifies the accepted items. Drafting does not publish them.

## 4. Run checks after certification

When you have permission to run checks, choose Run checks after the steward has certified the inputs. Wait for the job to finish and read its findings, candidate changes and eval pre report. Resolve failures and run fresh checks before asking the steward to sign off. A changed candidate needs fresh checks and sign-off.

## 5. Release

After the steward has signed off, choose Release and wait for completion. The release automatically runs eval post against the graph now served. Read the report in Versions: it is the latest check of a release, including a check after rollback. A failed post check needs investigation and recovery; it does not automatically restore the previous version.

## 6. Restore the previous release

Open Versions and review the current and previous releases. When recovery is needed, choose Roll back to the previous release. This restores the prior release's saved inputs and warehouse identity and leaves live access unchanged. Wait for completion and read the automatic eval post report before resuming use.

## 7. Review improvements

When Improve is available, choose Improve: Run for the business area and wait for the job. Review any proposal it keeps. A result with no proposal kept is not work ready for acceptance. A kept proposal follows business acceptance, certification by a different steward, fresh checks, sign-off and release.
