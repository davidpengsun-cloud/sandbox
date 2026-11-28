# Not mapped

Each line is a source fact that was not written into the pack. Nothing here was filled by guessing.

- `examples/sources/catalogue.html` Columns `branch_code` has no description. The column is not in the schema or the catalogue.
- `examples/sources/catalogue.html` DQ rules `entry_id` has no fail percentage. `fail_percent` was left absent, not set to 0.
- `examples/sources/catalogue.html` Notes name the owner Retail desk and give no email. Both owner emails are blank.
- `examples/sources/catalogue.html` Sources `apm_id` is empty. The catalogue writes `APM-PLACEHOLDER`, not a project id.
- `examples/sources/catalogue.html` path and schema are not in the source. The catalogue writes `placeholder`, not a service URL.
- `examples/sources/glossary.md` Ledger entry states a definition and does not state a domain, a steward, or that the definition is certified. The term was not written.
- `examples/sources/glossary.md` does not name a column for Ledger entry. `mappings` is empty.
- The notes do not name an access purpose. `access.purposes` is empty.
- `examples/sources/glossary.md` term Float has no definition.
- `examples/sources/documents/scan.docx` is not a format either reader declares. It was not shelved.
- `examples/sources/catalogue.html` Assets Rates API states no description. The portal-only card has none.
- `examples/sources/runtime-catalog.json` `bq_teller` has a blank `location` (`placeholder`) and proposes mapping `teller.ledger` -> `teller_ledger_mart` and `teller.ledger.amount` -> `amount_minor` (`INT64`) from lineage; extra table `teller_audit_log` and column `etl_loaded_at` are ignored, and `runtimes` is not written until the author confirms the proposals.
