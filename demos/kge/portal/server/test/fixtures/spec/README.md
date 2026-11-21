# Published export schemas

The portal's export tests validate each exported document against the standard's published JSON Schema. The schemas are copied here unchanged, so the tests run offline; the tests only read them and the portal image does not ship them.

| File | Standard | Source | Fetched | Licence |
|---|---|---|---|---|
| `odps-v4.2.json` | Open Data Product Standard (ODPS) 4.2 | <https://raw.githubusercontent.com/Open-Data-Product-Initiative/v4.2/main/source/schema/odps.json> | 2026-11-21 | Apache-2.0, [`LICENSE-ODPS`](LICENSE-ODPS) |
| `odcs-v3.2.0.json` | Open Data Contract Standard (ODCS) v3.2.0 | <https://raw.githubusercontent.com/bitol-io/open-data-contract-standard/main/schema/odcs-json-schema-v3.2.0.json> | 2026-11-21 | Apache-2.0, [`LICENSE-ODCS`](LICENSE-ODCS) |

Copyright: ODPS, 2022 Mindmote Oy and the Open Data Product Initiative contributors, as its licence file states. ODCS, the Bitol project (LF AI & Data Foundation) contributors; its licence file names no holder.

`LICENSE-ODPS` and `LICENSE-ODCS` are each repository's licence file as published, unchanged. To move to a later version of a standard, replace its schema here, change the version constant in `server/src/exports/`, and update this table.
