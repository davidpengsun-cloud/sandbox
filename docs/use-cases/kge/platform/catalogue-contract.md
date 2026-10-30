# Catalogue provider contract v1

The portal depends on a catalogue-source port, not on KGE. Its versioned wire contract is the JSON Schema at [`demos/kge/portal/server/contract/catalogue.v1.schema.json`](../../../../demos/kge/portal/server/contract/catalogue.v1.schema.json). Every provider returns that shape and passes the same conformance assertions before its catalogue may seed portal records or refresh the separate KGE-owned overlay.

## Envelope and change identity

`contract_version` is the string `"1"`. `release` is a required, non-empty provider identity: KGE uses the released graph identity and the file adapter uses the file's SHA-256 content hash. `has_catalogue` says whether this release has catalogue content. `products` is always an array. A provider can poll its cheaper identity operation before loading the full catalogue.

Unknown properties are refused at every level. Required properties are named by each schema object's `required` array; properties present under `properties` but absent from that array are optional. The file provider must supply every storage-bound field. A pack’s optional `catalogue.yaml` is descriptive enrichment, not a complete standalone provider file. The shipped standalone example is `examples/meridian-portal/catalogue.yaml`, exported from Meridian’s compiled catalogue.

## Product

Every product requires its stable `id`, `unit_id`, physical `table`, display `name`, `description`, `product_owner`, `business_owner`, `badges`, product `quality_score`, activity `counts`, `rating`, `domain`, `community`, `application`, `product_type`, `lineage`, and `fields`. These names are provider-neutral.

Operational facts `last_refresh`, `ingested_at`, `records`, `path`, `data_format`, `storage`, and `last_dq_run` are required. `steward` is optional. `product_owner` is accountable for the product, `business_owner` owns its business meaning, and `steward`, when supplied, names its day-to-day metadata and DQ steward. Counts are non-negative integers for subscriptions, access and views. Scores are percentages from 0 to 100; ratings are from 0 to 5. Storage is display text rather than a byte count, so providers do not imply a false common measurement.

Lineage requires arrays for origin paths, destination paths, upstream product IDs and downstream product IDs. The domain, community and application fields form the browsing hierarchy; the contract does not impose provider-specific hierarchy depth.

## Field and data quality

Every field requires `name`, `business_name`, `quality`, `terms`, `data_type`, and `description`. KGE derives the physical facts from its released model; standalone files must carry them. Terms are strings.

Each field's quality object requires its `dimension`, human-readable `rule`, `rule_type`, and `fail_percent`. The failure percentage is from 0 to 100. Product badges are portal-owned business metadata. Product score and field quality are KGE-owned facts in joined mode; the field rule remains the precise DQ statement.

## Providers and access

`PORTAL_CATALOGUE_PROVIDER=kge` reads KGE's `/catalogue/release` and `/catalogue/portal`. `PORTAL_CATALOGUE_PROVIDER=file` reads `PORTAL_CATALOGUE_FILE`, validates the source file's required fields and unknown keys, normalizes `unit` to `unit_id`, and validates the result against this schema. If the provider setting is omitted, `KGE_URL` selects KGE, otherwise `PORTAL_CATALOGUE_FILE` selects file; with neither, startup refuses and names both settings.

With KGE, KGE's signed-in principal and readable-unit response remain the access gate. Without KGE, the portal authenticates passwords through its own `/session` and requires its own session secret. Administrators supply KGE-format `principals.json` and `passwords.json` through `PORTAL_IDENTITIES_FILE` and `PORTAL_PASSWORDS_FILE`; passwords are salted scrypt hashes. A product is readable only when its `unit_id` is both in the catalogue and in the authenticated principal's explicit `reads` list. Principal and password files are reread on each request. The existing `_portal_users` role table governs portal actions only; neither a role nor a request header establishes identity or grants unit access. See the [demo setup](../../../../demos/kge/README.md#the-discovery-portal) for credential generation and session lifetime. Preview, export and query are unavailable without KGE. Search and catalogue Ask run over the portal tables; catalogue Ask gives no data answers or figures.

The portal polls on `PORTAL_CATALOGUE_SYNC_MS`. A seed inserts missing products and fields, preserving the existing portal records. When KGE is configured, its released catalogue refreshes a separate `_portal_governed` overlay even when a file supplied the initial business metadata. Browsing overlays physical tables and columns, row counts, storage, terms, lineage and quality; descriptive names, descriptions, business-stated owners, badges and browsing hierarchy stay in portal tables. Favorites, ratings, requests, roles and publish state also stay in portal tables. Invalid or unavailable copies leave the last good records available.

Search and Ask have their own factory-selected providers. They use KGE when configured and the portal tables alone, with the same response contract. An unreachable KGE triggers a visible catalogue fallback. A previously verified session may use its cached readable units for at most five minutes during an outage; authentication refusals and logout invalidate that cache. Fallback never grants data access. The [portal metadata job](../../../../demos/kge/README.md#portal-metadata-onboarding-job) exports only portal-owned metadata, then requires landing, business acceptance, steward certification and the usual checks, sign-off and release.
