import { followCatalogue, followedFieldColumns, followedFields, heldCards, metadataPatch, overlay, qualityRules, settleCards } from "../catalogue-record.js";
import { BigQuery } from "@google-cloud/bigquery";
import { randomUUID } from "node:crypto";
import { normalizeUsername } from "../portal-access.js";
import { checkedActor, checkedRole, derivedSchemaVersion, explicitRoles, formerlySeededGrants, migrationEntries, primaryRole, seededGrantsRemoval, tables } from "./schema.js";

function table(dataset, name) {
  return `\`${dataset}.\_portal_${name}\``;
}

const schemas = {
  governed: "singleton INT64 NOT NULL, payload STRING NOT NULL",
  products: "id STRING NOT NULL, unit_id STRING NOT NULL, table_name STRING NOT NULL, name STRING NOT NULL, description STRING NOT NULL, product_owner STRING NOT NULL, business_owner STRING NOT NULL, badges STRING NOT NULL, quality_score FLOAT64 NOT NULL, last_refresh STRING NOT NULL, ingested_at STRING NOT NULL, subscribe_count INT64 NOT NULL, access_count INT64 NOT NULL, view_count INT64 NOT NULL, baseline_rating FLOAT64 NOT NULL, domain STRING NOT NULL, community STRING NOT NULL, application STRING NOT NULL, product_type STRING NOT NULL, record_count INT64 NOT NULL, path STRING NOT NULL, data_format STRING NOT NULL, storage STRING NOT NULL, last_dq_run STRING NOT NULL, asset_type STRING, usage_purpose STRING, dataset_type STRING, APM_ID STRING, project STRING, product_owner_email STRING, business_owner_email STRING, refresh_cadence STRING, schema STRING",
  fields: "product_id STRING NOT NULL, ordinal INT64 NOT NULL, name STRING NOT NULL, business_name STRING NOT NULL, data_type STRING NOT NULL, description STRING NOT NULL, terms STRING NOT NULL, classification STRING, pii BOOL",
  lineage: "product_id STRING NOT NULL, direction STRING NOT NULL, ordinal INT64 NOT NULL, value STRING NOT NULL",
  quality_rules: "product_id STRING NOT NULL, field_name STRING NOT NULL, dimension STRING NOT NULL, rule_name STRING NOT NULL, rule_type STRING NOT NULL, fail_percent FLOAT64, description STRING, rule_ordinal INT64",
  catalogue_sync: "singleton INT64 NOT NULL, release_id STRING NOT NULL, synced_at TIMESTAMP NOT NULL",
  users: "principal_id STRING NOT NULL, role STRING NOT NULL",
  role_audit: "id STRING NOT NULL, `at` TIMESTAMP NOT NULL, actor STRING NOT NULL, principal_id STRING NOT NULL, before_roles STRING NOT NULL, after_roles STRING NOT NULL",
  identity_migrations: "principal_id STRING NOT NULL, username STRING NOT NULL, `at` TIMESTAMP NOT NULL, actor STRING NOT NULL",
  upgrades: "name STRING NOT NULL, `at` TIMESTAMP NOT NULL",
  favourites: "principal_id STRING NOT NULL, product_id STRING NOT NULL",
  views: "principal_id STRING NOT NULL, product_id STRING NOT NULL, viewed_at TIMESTAMP NOT NULL",
  cart: "principal_id STRING NOT NULL, product_id STRING NOT NULL",
  requests: "id STRING NOT NULL, principal_id STRING NOT NULL, product_id STRING NOT NULL, kind STRING NOT NULL, status STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  feedback: "id STRING NOT NULL, principal_id STRING NOT NULL, product_id STRING NOT NULL, rating INT64 NOT NULL, comment STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  ask_feedback: "id STRING NOT NULL, principal_id STRING NOT NULL, session_id INT64 NOT NULL, question_id INT64 NOT NULL, scope STRING NOT NULL, direction STRING NOT NULL, comment STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  publish_status: "product_id STRING NOT NULL, published BOOL NOT NULL",
  schema: "singleton INT64 NOT NULL, derived_version INT64 NOT NULL",
};

// The columns added to a table after it first shipped: an older table without
// them is still the expected shape, and startup adds them.
const addedColumns = { products: ["asset_type", "usage_purpose", "dataset_type", "APM_ID", "project", "product_owner_email", "business_owner_email", "refresh_cadence", "schema"], fields: ["classification", "pii"], quality_rules: ["description", "rule_ordinal"] };

// Each table's column types, read from the schema above, so a query parameter
// bound into a table is typed by the table's own columns.
const columnTypes = (name) => Object.fromEntries(schemas[name].split(", ").map((column) => column.split(" ").slice(0, 2)));

export function openBigQueryRepository(env, options = {}) {
  const client = options.client || new BigQuery({ projectId: env.BIGQUERY_PROJECT });
  if (!env.PROFILE) throw new Error("PROFILE is required for the BigQuery portal repository");
  const profile = env.PROFILE.replaceAll("-", "_");
  const dataset = `${env.BIGQUERY_DATASET_PREFIX || "kge"}_${profile}_warehouses`;
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const rateLimited = (error) => error?.code === 429 || error?.errors?.some((item) => ["rateLimitExceeded", "jobRateLimitExceeded"].includes(item?.reason)) || /Exceeded rate limits|rateLimitExceeded/i.test(String(error?.message || ""));
  const transient = (error) => rateLimited(error) || error?.code === 503 || error?.errors?.some((item) => item?.reason === "backendError");
  const query = async (sql, params = {}, types) => {
    const job = types ? { query: sql, params, types } : { query: sql, params };
    const read = /^\s*SELECT\b/i.test(sql);
    for (let attempt = 0; ; attempt += 1) {
      try { return (await client.query(job))[0]; }
      catch (error) {
        if (!(read ? transient(error) : rateLimited(error)) || attempt >= 4) throw Object.assign(error, transient(error) ? { status: 503 } : {});
        await sleep(Math.min(2000, 100 * (2 ** attempt)) + Math.floor(Math.random() * 50));
      }
    }
  };
  const initialize = async () => {
    const derived = ["products", "fields", "lineage", "quality_rules", "catalogue_sync"];
    await query(`CREATE TABLE IF NOT EXISTS ${table(dataset, "schema")} (${schemas.schema})`);
    const expected = Object.fromEntries(derived.map((name) => [name, schemas[name].split(", ").map((column) => column.split(" ")[0])]));
    const held = await query(`SELECT table_name,column_name,ordinal_position,is_nullable FROM \`${dataset}.INFORMATION_SCHEMA.COLUMNS\` WHERE table_name IN UNNEST(@tables) ORDER BY table_name,ordinal_position`, { tables: derived.map((name) => `_portal_${name}`) }, { tables: ["STRING"] });
    const actual = new Map();
    // A blank fail % is NULL: a table made before it was allowed drops the
    // constraint in the same script that adds the newer columns.
    const failRequired = held.some((column) => column.table_name === "_portal_quality_rules" && column.column_name === "fail_percent" && column.is_nullable === "NO");
    for (const column of held) {
      const name = column.table_name.replace(/^_portal_/, "");
      actual.set(name, [...(actual.get(name) || []), column.column_name]);
    }
    const shapeChanged = derived.some((name) => {
      const compatible = expected[name].filter((column) => !(addedColumns[name] || []).includes(column) || actual.get(name)?.includes(column));
      return actual.has(name) && (actual.get(name).length !== compatible.length || actual.get(name).some((column, index) => column !== compatible[index]));
    });
    if (shapeChanged) throw new Error("Portal record schema needs a migration; existing records were preserved.");
    for (const name of tables.filter((name) => name !== "schema")) await query(`CREATE TABLE IF NOT EXISTS ${table(dataset, name)} (${schemas[name]})`);
    await query(Object.entries(addedColumns).map(([name, columns]) => `ALTER TABLE ${table(dataset, name)} ${columns.map((column) => `ADD COLUMN IF NOT EXISTS ${column} ${columnTypes(name)[column]}`).join(", ")}`).concat(failRequired ? [`ALTER TABLE ${table(dataset, "quality_rules")} ALTER COLUMN fail_percent DROP NOT NULL`] : []).join(";\n"));
    await query(`MERGE ${table(dataset, "schema")} t USING (SELECT 1 singleton,@version derived_version) s ON t.singleton=s.singleton WHEN MATCHED THEN UPDATE SET derived_version=s.derived_version WHEN NOT MATCHED THEN INSERT ROW`, { version: derivedSchemaVersion });
    await query(`ALTER TABLE ${table(dataset, "requests")} ADD COLUMN IF NOT EXISTS decided_by STRING, ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP`);
    await query(`BEGIN TRANSACTION;
UPDATE ${table(dataset, "schema")} SET derived_version=derived_version WHERE singleton=1;
IF NOT EXISTS (SELECT 1 FROM ${table(dataset, "upgrades")} WHERE name=@name) THEN
CREATE TEMP TABLE seeded_removal AS SELECT s.principal,s.role,ARRAY_AGG(DISTINCT u.role ORDER BY u.role) before_roles FROM UNNEST(@seeds) s JOIN ${table(dataset, "users")} t ON t.principal_id=s.principal AND t.role=s.role JOIN ${table(dataset, "users")} u ON u.principal_id=s.principal GROUP BY s.principal,s.role;
DELETE FROM ${table(dataset, "users")} t WHERE EXISTS (SELECT 1 FROM seeded_removal r WHERE r.principal=t.principal_id AND r.role=t.role);
INSERT ${table(dataset, "role_audit")} SELECT GENERATE_UUID(),CURRENT_TIMESTAMP(),'upgrade',r.principal,TO_JSON_STRING(r.before_roles),TO_JSON_STRING(ARRAY(SELECT held FROM UNNEST(r.before_roles) held WHERE held<>r.role ORDER BY held)) FROM seeded_removal r;
INSERT ${table(dataset, "upgrades")} VALUES (@name,CURRENT_TIMESTAMP());
END IF;
COMMIT TRANSACTION;`, { name: seededGrantsRemoval, seeds: formerlySeededGrants.map((grant) => ({ ...grant })) }, { name: "STRING", seeds: [{ principal: "STRING", role: "STRING" }] });
  };
  let ready;
  const run = async (sql, params, types) => {
    ready ??= initialize().catch((error) => { ready = undefined; throw error; });
    await ready;
    return query(sql, params, types);
  };
  const auditEntry = (row) => ({ id: row.id, at: row.at?.value || row.at, by: row.actor, principal: row.principal_id, before: JSON.parse(row.before_roles), after: JSON.parse(row.after_roles) });
  const roles = async (principal) => {
    const held = explicitRoles((await run(`SELECT DISTINCT role FROM ${table(dataset, "users")} WHERE principal_id=@principal ORDER BY role`, { principal })).map(({ role }) => role));
    return held.length ? held : ["viewer"];
  };
  const writeRole = async (principal, role, grant, by, replace = false) => {
    principal = normalizeUsername(principal); checkedActor(by); checkedRole(role);
    if (typeof grant !== "boolean") throw new Error("A grant or removal is required");
    const entry = (await run(`DECLARE before_roles ARRAY<STRING>;
BEGIN TRANSACTION;
UPDATE ${table(dataset, "schema")} SET derived_version=derived_version WHERE singleton=1;
SET before_roles = ARRAY(SELECT DISTINCT role FROM ${table(dataset, "users")} WHERE principal_id=@principal ORDER BY role);
${replace ? `DELETE FROM ${table(dataset, "users")} WHERE principal_id=@principal;` : ""}
${grant ? `MERGE ${table(dataset, "users")} t USING (SELECT @principal principal_id,@role role) s ON t.principal_id=s.principal_id AND t.role=s.role WHEN NOT MATCHED THEN INSERT ROW;` : `DELETE FROM ${table(dataset, "users")} WHERE principal_id=@principal AND role=@role;`}
INSERT ${table(dataset, "role_audit")} VALUES (@id,CURRENT_TIMESTAMP(),@by,@principal,TO_JSON_STRING(before_roles),TO_JSON_STRING(ARRAY(SELECT DISTINCT role FROM ${table(dataset, "users")} WHERE principal_id=@principal ORDER BY role)));
COMMIT TRANSACTION;
SELECT * FROM ${table(dataset, "role_audit")} WHERE id=@id;`, { principal, role, by, id: randomUUID() }, { principal: "STRING", role: "STRING", by: "STRING", id: "STRING" }))[0];
    return entry ? auditEntry(entry) : undefined;
  };
  const migrateIdentities = async (map, by = "migration") => {
    const entries = migrationEntries(map); by = normalizeUsername(by);
    if (!entries.length) return { migrated: 0, skipped: 0 };
    const result = await run(`BEGIN TRANSACTION;
UPDATE ${table(dataset, "schema")} SET derived_version=derived_version WHERE singleton=1;
ASSERT NOT EXISTS (SELECT 1 FROM UNNEST(@entries) m JOIN ${table(dataset, "identity_migrations")} h ON h.principal_id=m.principal WHERE h.username<>m.username) AS 'An identity was already migrated to another username';
ASSERT NOT EXISTS (SELECT 1 FROM UNNEST(@entries) m JOIN ${table(dataset, "identity_migrations")} h ON h.username=m.principal WHERE h.principal_id<>m.principal AND m.principal<>m.username) AS 'Identity migration chains are ambiguous';
ASSERT NOT EXISTS (SELECT 1 FROM UNNEST(@entries) m JOIN ${table(dataset, "identity_migrations")} h ON h.principal_id=m.username WHERE h.username<>m.username) AS 'Identity migration chains are ambiguous';
CREATE TEMP TABLE migration_work AS SELECT m.* FROM UNNEST(@entries) m WHERE NOT EXISTS (SELECT 1 FROM ${table(dataset, "identity_migrations")} h WHERE h.principal_id=m.principal);
${[["users", "role"], ["favourites", "product_id"], ["cart", "product_id"]].map(([name, column]) => `MERGE ${table(dataset, name)} t USING (SELECT DISTINCT m.username principal_id,t.${column} FROM ${table(dataset, name)} t JOIN migration_work m ON m.principal=t.principal_id WHERE m.principal<>m.username) s ON t.principal_id=s.principal_id AND t.${column}=s.${column} WHEN NOT MATCHED THEN INSERT ROW;
DELETE FROM ${table(dataset, name)} WHERE principal_id IN (SELECT principal FROM migration_work WHERE principal<>username);`).join("\n")}
${["views", "requests", "feedback", "ask_feedback", "role_audit"].map((name) => `UPDATE ${table(dataset, name)} t SET principal_id=m.username FROM migration_work m WHERE t.principal_id=m.principal AND m.principal<>m.username;`).join("\n")}
UPDATE ${table(dataset, "requests")} t SET decided_by=m.username FROM migration_work m WHERE t.decided_by=m.principal AND m.principal<>m.username;
UPDATE ${table(dataset, "role_audit")} t SET actor=m.username FROM migration_work m WHERE t.actor=m.principal AND m.principal<>m.username;
INSERT ${table(dataset, "identity_migrations")} SELECT principal,username,CURRENT_TIMESTAMP(),@by FROM migration_work;
COMMIT TRANSACTION;
SELECT COUNT(*) migrated,ARRAY_LENGTH(@entries)-COUNT(*) skipped FROM migration_work;`, { entries, by }, { entries: [{ principal: "STRING", username: "STRING" }], by: "STRING" });
    return result[0] || { migrated: 0, skipped: 0 };
  };
  const owned = (name, order = "", columns = "t.product_id") => (principal, units) => run(`SELECT ${columns} FROM ${table(dataset, name)} t JOIN ${table(dataset, "products")} p ON p.id=t.product_id AND p.unit_id IN UNNEST(@units) WHERE t.principal_id=@principal${order}`, { principal, units }, { units: ["STRING"] });
  const emptyLineage = () => ({ origin_paths: [], destination_paths: [], upstream: [], downstream: [] });
  const productRows = async (units, search = "", selection, onIds) => {
    const params = { units, search, ...(selection ? { id: selection.id ?? null } : {}) };
    const types = { units: ["STRING"], ...(selection ? { id: "STRING" } : {}) };
    // These subqueries are uncorrelated: visibility covers every readable
    // product, even when the selected cards are limited by search or id.
    const [context = {}] = await run(`SELECT
ARRAY(SELECT AS STRUCT p.* FROM ${table(dataset, "products")} p WHERE unit_id IN UNNEST(@units) AND (@search='' OR STRPOS(LOWER(CONCAT(name,' ',description,' ',domain,' ',community,' ',application)),LOWER(@search))>0)${selection ? " AND id=@id" : ""} ORDER BY name) products,
ARRAY(SELECT id FROM ${table(dataset, "products")} WHERE unit_id IN UNNEST(@units)) visible_ids,
(SELECT payload FROM ${table(dataset, "governed")} WHERE singleton=1 LIMIT 1) governed_payload`, params, types);
    const found = context.products || [];
    const ids = found.map((row) => row.id);
    const children = Promise.all([
      run(`SELECT f.*,q.dimension,q.rule_name,q.rule_type,q.fail_percent,q.description AS rule_description FROM ${table(dataset, "fields")} f JOIN ${table(dataset, "quality_rules")} q ON q.product_id=f.product_id AND q.field_name=f.name WHERE f.product_id IN UNNEST(@ids) ORDER BY f.product_id,f.ordinal,IFNULL(q.rule_ordinal,0)`, { ids }, { ids: ["STRING"] }),
      run(`SELECT product_id,direction,value FROM ${table(dataset, "lineage")} WHERE product_id IN UNNEST(@ids) ORDER BY product_id,direction,ordinal`, { ids }, { ids: ["STRING"] }),
    ]);
    children.catch(() => {});
    if (typeof onIds === "function") onIds(ids);
    const [fields, lineage] = await children;
    const fieldRows = new Map(), lineageRows = new Map();
    for (const field of fields) {
      if (!fieldRows.has(field.product_id)) fieldRows.set(field.product_id, []);
      const held = fieldRows.get(field.product_id), rule = { dimension: field.dimension, rule: field.rule_name, rule_type: field.rule_type, fail_percent: field.fail_percent === null || field.fail_percent === undefined ? null : Number(field.fail_percent), description: field.rule_description || "" };
      if (held.at(-1)?.name === field.name) held.at(-1).quality = [...qualityRules(held.at(-1).quality), rule];
      else held.push({ name: field.name, business_name: field.business_name, data_type: field.data_type, description: field.description, terms: JSON.parse(field.terms || "[]"), classification: field.classification || "", pii: field.pii === true, quality: rule });
    }
    for (const row of lineage) {
      if (!lineageRows.has(row.product_id)) lineageRows.set(row.product_id, emptyLineage());
      lineageRows.get(row.product_id)[row.direction].push(row.value);
    }
    const products = found.map((row) => ({ id: row.id, unit_id: row.unit_id, table: row.table_name, name: row.name, description: row.description, product_owner: row.product_owner, business_owner: row.business_owner, badges: JSON.parse(row.badges || "[]"), quality_score: Number(row.quality_score), last_refresh: row.last_refresh, ingested_at: row.ingested_at, counts: { subscribe: Number(row.subscribe_count), access: Number(row.access_count), views: Number(row.view_count) }, rating: Number(row.baseline_rating), domain: row.domain, community: row.community, application: row.application, product_type: row.product_type, records: Number(row.record_count), path: row.path, data_format: row.data_format, storage: row.storage, last_dq_run: row.last_dq_run, asset_type: row.asset_type || "", usage_purpose: row.usage_purpose || "", dataset_type: row.dataset_type || "", APM_ID: row.APM_ID || "", project: row.project || "", product_owner_email: row.product_owner_email || "", business_owner_email: row.business_owner_email || "", refresh_cadence: row.refresh_cadence || "", schema: row.schema || "", fields: fieldRows.get(row.id) || [], lineage: lineageRows.get(row.id) || emptyLineage() }));
    const visible = new Set(context.visible_ids || []);
    const current = context.governed_payload == null ? null : JSON.parse(context.governed_payload);
    const active = context.governed_payload != null ? new Set((current.products || []).map((p) => `${p.unit_id}:${p.id}`)) : null;
    const governed = new Map((context.governed_payload != null ? current.products || [] : []).map((p) => [p.id, p]));
    const live = active ? new Set([...governed.values()].filter((p) => visible.has(p.id) && units.includes(p.unit_id)).map((p) => p.id)) : visible;
    return products.filter((p) => !active || active.has(`${p.unit_id}:${p.id}`)).map((p) => overlay(p, governed.get(p.id), live)).map((product) => ({ ...product, lineage: { ...product.lineage, upstream: product.lineage.upstream.filter((id) => visible.has(id)), downstream: product.lineage.downstream.filter((id) => visible.has(id)) } }));
  };
  const syncCatalogue = async (release, products, mode = false) => {
    const seedOnly = Boolean(mode), follow = mode === "follow";
    const synced = (await run(`SELECT release_id FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1 LIMIT 1`))[0]?.release_id;
    if (synced === release) return false;
    const payload = (await run(`SELECT payload FROM ${table(dataset, "governed")} WHERE singleton=1 LIMIT 1`))[0]?.payload;
    let previous = null;
    try { previous = payload ? JSON.parse(payload) : null; } catch { previous = null; }
    const held = follow ? heldCards(await run(`SELECT id,${followedFields.join(",")},badges FROM ${table(dataset, "products")}`), await run(`SELECT product_id,name,${followedFieldColumns.join(",")} FROM ${table(dataset, "fields")}`)) : new Map();
    const followed = followCatalogue(previous, held, products || [], follow);
    const catalogue = JSON.stringify({ release, products: products || [], cards: followed.cards });
    // Each write applies only while the card still holds the value the sync
    // read, so an edit made between the read and this transaction stays.
    const productWrites = followed.products.length ? [...followedFields, "badges"].map((column) => `UPDATE ${table(dataset, "products")} p SET ${column}=w.value FROM UNNEST(@productWrites) w WHERE w.target='${column}' AND p.id=w.id AND p.${column}=w.held;`).join("\n") : "";
    const fieldWrites = followed.fields.length ? followedFieldColumns.map((column) => `UPDATE ${table(dataset, "fields")} f SET ${column}=w.value FROM UNNEST(@fieldWrites) w WHERE w.target='${column}' AND f.product_id=w.product_id AND f.name=w.name AND f.${column}=w.held;`).join("\n") : "";
    // The values the writes left, read back so the memory records an edit that
    // pre-empted a write rather than the value the sync meant to write.
    const settled = [
      ...(productWrites ? [`SELECT w.id AS product_id,'' AS name,w.target,CASE w.target ${[...followedFields, "badges"].map((column) => `WHEN '${column}' THEN p.${column}`).join(" ")} END AS actual FROM UNNEST(@productWrites) w JOIN ${table(dataset, "products")} p ON p.id=w.id`] : []),
      ...(fieldWrites ? [`SELECT w.product_id,w.name,w.target,IF(w.target='business_name',f.business_name,f.description) AS actual FROM UNNEST(@fieldWrites) w JOIN ${table(dataset, "fields")} f ON f.product_id=w.product_id AND f.name=w.name`] : []),
    ].join(" UNION ALL ");
    const writeParams = { ...(productWrites ? { productWrites: followed.products } : {}), ...(fieldWrites ? { fieldWrites: followed.fields } : {}) };
    const writeTypes = { ...(productWrites ? { productWrites: [{ id: "STRING", target: "STRING", held: "STRING", value: "STRING" }] } : {}), ...(fieldWrites ? { fieldWrites: [{ product_id: "STRING", name: "STRING", target: "STRING", held: "STRING", value: "STRING" }] } : {}) };
    // Every parameter carries its column's type: left to infer, the client
    // types an array of structs from its first row alone, so a first
    // quality_score of 0 would make the whole column INT64 (#159).
    const heldTypes = { catalogue: columnTypes("governed").payload, release: columnTypes("catalogue_sync").release_id };
    if (products === null) {
      await run(`BEGIN TRANSACTION; DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue); DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;`, { catalogue, release: release || "unreleased" }, heldTypes);
      return true;
    }
    const productRows = products.map((p) => ({ id: p.id, unit_id: p.unit_id, table_name: p.table, name: p.name, description: p.description, product_owner: p.product_owner, business_owner: p.business_owner, badges: JSON.stringify(p.badges), quality_score: p.quality_score, last_refresh: p.last_refresh, ingested_at: p.ingested_at, subscribe_count: p.counts.subscribe, access_count: p.counts.access, view_count: p.counts.views, baseline_rating: p.rating, domain: p.domain, community: p.community, application: p.application, product_type: p.product_type, record_count: p.records ?? 0, path: p.path, data_format: p.data_format, storage: p.storage, last_dq_run: p.last_dq_run, asset_type: p.asset_type || "", usage_purpose: p.usage_purpose || "", dataset_type: p.dataset_type || "", APM_ID: p.APM_ID || "", project: p.project || "", product_owner_email: p.product_owner_email || "", business_owner_email: p.business_owner_email || "", refresh_cadence: p.refresh_cadence || "", schema: p.schema || "" }));
    const fieldRows = products.flatMap((p) => p.fields.map((f, ordinal) => ({ product_id: p.id, ordinal, name: f.name, business_name: f.business_name, data_type: f.data_type, description: f.description, terms: JSON.stringify(f.terms || []), classification: f.classification || "", pii: f.pii === true })));
    const qualityRows = products.flatMap((p) => p.fields.flatMap((f) => qualityRules(f.quality).map((rule, rule_ordinal) => ({ product_id: p.id, field_name: f.name, dimension: rule.dimension, rule_name: rule.rule, rule_type: rule.rule_type, fail_percent: rule.fail_percent ?? null, description: rule.description || "", rule_ordinal }))));
    const lineageRows = products.flatMap((p) => ["origin_paths", "destination_paths", "upstream", "downstream"].flatMap((direction) => p.lineage[direction].map((value, ordinal) => ({ product_id: p.id, direction, ordinal, value }))));
    if (!products.length && seedOnly) return syncCatalogue(release, null, mode);
    if (!products.length) {
      await run(`BEGIN TRANSACTION; DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue); DELETE FROM ${table(dataset, "quality_rules")} WHERE TRUE; DELETE FROM ${table(dataset, "fields")} WHERE TRUE; DELETE FROM ${table(dataset, "lineage")} WHERE TRUE; DELETE FROM ${table(dataset, "products")} WHERE TRUE; DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;`, { catalogue, release: release || "unreleased" }, heldTypes);
      return true;
    }
    const insert = (name, parameter, key) => seedOnly
      ? `MERGE ${table(dataset, name)} t USING (SELECT * FROM UNNEST(@${parameter})) s ON ${key} ${name === "products" ? "WHEN MATCHED THEN UPDATE SET unit_id=s.unit_id,table_name=s.table_name,asset_type=s.asset_type,usage_purpose=s.usage_purpose,dataset_type=s.dataset_type,APM_ID=s.APM_ID,project=s.project,refresh_cadence=s.refresh_cadence,path=s.path,data_format=s.data_format,schema=s.schema " : name === "fields" ? "WHEN MATCHED THEN UPDATE SET classification=s.classification,pii=s.pii " : name === "quality_rules" ? "WHEN MATCHED THEN UPDATE SET description=s.description " : ""}WHEN NOT MATCHED THEN INSERT ROW;`
      : `INSERT ${table(dataset, name)} SELECT * FROM UNNEST(@${parameter});`;
    const actual = await run(`BEGIN TRANSACTION;
${seedOnly ? `UPDATE ${table(dataset, "products")} p SET product_owner=IF(p.product_owner=JSON_VALUE(old,'$.product_owner'),fresh.product_owner,p.product_owner),business_owner=IF(p.business_owner=JSON_VALUE(old,'$.business_owner'),fresh.business_owner,p.business_owner),product_owner_email=IF(IFNULL(p.product_owner_email,'')=IFNULL(JSON_VALUE(old,'$.product_owner_email'),''),fresh.product_owner_email,p.product_owner_email),business_owner_email=IF(IFNULL(p.business_owner_email,'')=IFNULL(JSON_VALUE(old,'$.business_owner_email'),''),fresh.business_owner_email,p.business_owner_email) FROM UNNEST(@products) fresh,${table(dataset, "governed")} g,UNNEST(JSON_QUERY_ARRAY(g.payload,'$.products')) old WHERE g.singleton=1 AND p.id=fresh.id AND p.id=JSON_VALUE(old,'$.id');` : ""}
${productWrites}
${fieldWrites}
DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue);
${seedOnly ? "" : `DELETE FROM ${table(dataset, "quality_rules")} WHERE TRUE; DELETE FROM ${table(dataset, "fields")} WHERE TRUE; DELETE FROM ${table(dataset, "lineage")} WHERE TRUE; DELETE FROM ${table(dataset, "products")} WHERE TRUE;`}
${insert("products", "products", "t.id=s.id")}
${fieldRows.length ? insert("fields", "fields", "t.product_id=s.product_id AND t.name=s.name") + insert("quality_rules", "quality", "t.product_id=s.product_id AND t.field_name=s.field_name AND IFNULL(t.rule_ordinal,0)=s.rule_ordinal") : ""}
${lineageRows.length ? insert("lineage", "lineage", "t.product_id=s.product_id AND t.direction=s.direction AND t.ordinal=s.ordinal") : ""}
DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;${settled ? `\n${settled};` : ""}`, { products: productRows, ...(fieldRows.length ? { fields: fieldRows, quality: qualityRows } : {}), ...(lineageRows.length ? { lineage: lineageRows } : {}), ...writeParams, catalogue, release: release || "unreleased" },
    { products: [columnTypes("products")], ...(fieldRows.length ? { fields: [columnTypes("fields")], quality: [columnTypes("quality_rules")] } : {}), ...(lineageRows.length ? { lineage: [columnTypes("lineage")] } : {}), ...writeTypes, ...heldTypes });
    if (settled && settleCards(followed.cards, actual)) {
      await run(`UPDATE ${table(dataset, "governed")} SET payload=@settled WHERE singleton=1 AND payload=@catalogue`, { settled: JSON.stringify({ release, products, cards: followed.cards }), catalogue }, { settled: "STRING", catalogue: "STRING" });
    }
    return true;
  };
  return {
    setGoverned: (catalogue) => run(`MERGE ${table(dataset, "governed")} t USING (SELECT 1 singleton,@payload payload) s ON t.singleton=s.singleton WHEN MATCHED THEN UPDATE SET payload=s.payload WHEN NOT MATCHED THEN INSERT ROW`, { payload: JSON.stringify(catalogue) }),
    governed: async () => { const row = (await run(`SELECT payload FROM ${table(dataset, "governed")} WHERE singleton=1 LIMIT 1`))[0]; return row ? JSON.parse(row.payload) : null; },
    updateMetadata: async (id, patch) => {
      metadataPatch(patch);
      const statements = [], params = { id };
      let index = 0;
      for (const [key, value] of Object.entries(patch)) {
        if (key === "fields") for (const field of value) {
          const fieldParam = `field${index++}`; params[fieldParam] = field.name;
          for (const [name, text] of Object.entries(field)) if (name !== "name") {
            const param = `value${index++}`; params[param] = text;
            statements.push(`UPDATE ${table(dataset, "fields")} SET ${name}=@${param} WHERE product_id=@id AND name=@${fieldParam}`);
          }
        }
        else { const param = `value${index++}`; params[param] = key === "badges" ? JSON.stringify(value) : value; statements.push(`UPDATE ${table(dataset, "products")} SET ${key}=@${param} WHERE id=@id`); }
      }
      if (statements.length) await run(`BEGIN TRANSACTION; ${statements.join("; ")}; COMMIT TRANSACTION;`, params);
    },
    tableNames: () => tables.map((name) => `_portal_${name}`),
    syncCatalogue, catalogueRelease: async () => (await run(`SELECT release_id FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1 LIMIT 1`))[0]?.release_id,
    listProducts: (units, search = "", onIds) => productRows(units, search, undefined, onIds), product: async (id, units) => (await productRows(units, "", { id }))[0], fields: async (id, units) => (await productRows(units, "", { id }))[0]?.fields || [], lineage: async (id, units) => (await productRows(units, "", { id }))[0]?.lineage || emptyLineage(),
    roles, role: async (principal) => primaryRole(await roles(principal)),
    changeRole: (principal, role, grant, by) => writeRole(principal, role, grant, by),
    setRole: (principal, role) => writeRole(principal, role, true, principal, true),
    roleAudit: async () => (await run(`SELECT * FROM ${table(dataset, "role_audit")} ORDER BY \`at\` DESC,id DESC`)).map(auditEntry),
    migrateIdentities,
    view: (principal, id) => run(`INSERT ${table(dataset, "views")} VALUES (@principal,@id,CURRENT_TIMESTAMP())`, { principal, id }),
    users: async () => {
      const people = new Map();
      for (const { principal_id, role } of await run(`SELECT principal_id,role FROM ${table(dataset, "users")} ORDER BY principal_id,role`)) people.set(principal_id, [...(people.get(principal_id) || []), role]);
      return [...people].map(([principal_id, held]) => ({ principal_id, role: primaryRole(held), roles: explicitRoles(held) }));
    },
    published: async (id) => (await run(`SELECT published FROM ${table(dataset, "publish_status")} WHERE product_id=@id LIMIT 1`, { id }))[0]?.published,
    publication: (ids) => run(`SELECT product_id,published FROM ${table(dataset, "publish_status")} WHERE product_id IN UNNEST(@ids)`, { ids }, { ids: ["STRING"] }),
    setPublished: (id, published) => run(`MERGE ${table(dataset, "publish_status")} t USING (SELECT @id product_id,@published published) s ON t.product_id=s.product_id WHEN MATCHED THEN UPDATE SET published=s.published WHEN NOT MATCHED THEN INSERT ROW`, { id, published }),
    favourites: owned("favourites"),
    addFavourite: (principal, id) => run(`MERGE ${table(dataset, "favourites")} t USING (SELECT @principal principal_id,@id product_id) s ON t.principal_id=s.principal_id AND t.product_id=s.product_id WHEN NOT MATCHED THEN INSERT ROW`, { principal, id }),
    removeFavourite: (principal, id) => run(`DELETE FROM ${table(dataset, "favourites")} WHERE principal_id=@principal AND product_id=@id`, { principal, id }),
    cart: owned("cart"),
    addCart: (principal, id) => run(`MERGE ${table(dataset, "cart")} t USING (SELECT @principal principal_id,@id product_id) s ON t.principal_id=s.principal_id AND t.product_id=s.product_id WHEN NOT MATCHED THEN INSERT ROW`, { principal, id }),
    removeCart: (principal, id) => run(`DELETE FROM ${table(dataset, "cart")} WHERE principal_id=@principal AND product_id=@id`, { principal, id }),
    emptyCart: (principal) => run(`DELETE FROM ${table(dataset, "cart")} WHERE principal_id=@principal`, { principal }),
    request: (principal, id, kind) => run(`INSERT ${table(dataset, "requests")} (id,principal_id,product_id,kind,status,created_at) VALUES (GENERATE_UUID(),@principal,@id,@kind,'OPEN',CURRENT_TIMESTAMP())`, { principal, id, kind }),
    requests: owned("requests", " ORDER BY t.created_at DESC", "t.*"),
    queue: () => run(`SELECT * FROM ${table(dataset, "requests")} WHERE status='OPEN' ORDER BY created_at`),
    pending: async (id) => (await run(`SELECT * FROM ${table(dataset, "requests")} WHERE id=@id AND status='OPEN' LIMIT 1`, { id: String(id ?? "") }))[0],
    decide: (id, status, approver) => run(`UPDATE ${table(dataset, "requests")} SET status=@status, decided_by=@approver, decided_at=CURRENT_TIMESTAMP() WHERE id=@id AND status='OPEN'`, { id: String(id), status, approver }),
    feedback: (principal, id, rating, comment) => run(`INSERT ${table(dataset, "feedback")} VALUES (GENERATE_UUID(),@principal,@id,@rating,@comment,CURRENT_TIMESTAMP())`, { principal, id, rating, comment }),
    askFeedback: (principal, session, question, scope, direction, comment) => run(`INSERT ${table(dataset, "ask_feedback")} VALUES (GENERATE_UUID(),@principal,@session,@question,@scope,@direction,@comment,CURRENT_TIMESTAMP())`, { principal, session, question, scope: JSON.stringify(scope), direction, comment }),
    averageFeedback: async (id) => (await run(`SELECT AVG(rating) average,COUNT(*) count FROM ${table(dataset, "feedback")} WHERE product_id=@id`, { id }))[0],
    activity: async (id) => (await run(`SELECT (SELECT COUNT(*) FROM ${table(dataset, "views")} WHERE product_id=@id) views,(SELECT COUNTIF(kind!='subscription') FROM ${table(dataset, "requests")} WHERE product_id=@id) access,(SELECT COUNTIF(kind='subscription') FROM ${table(dataset, "requests")} WHERE product_id=@id) subscribe,(SELECT AS STRUCT AVG(rating) average,COUNT(*) count FROM ${table(dataset, "feedback")} WHERE product_id=@id) feedback`, { id }))[0],
    activities: (ids) => run(`SELECT p product_id,IFNULL(v.views,0) views,IFNULL(r.access,0) access,IFNULL(r.subscribe,0) subscribe,STRUCT(f.average AS average,IFNULL(f.count,0) AS count) feedback FROM UNNEST(@ids) p LEFT JOIN (SELECT product_id,COUNT(*) views FROM ${table(dataset, "views")} WHERE product_id IN UNNEST(@ids) GROUP BY product_id) v ON v.product_id=p LEFT JOIN (SELECT product_id,COUNTIF(kind!='subscription') access,COUNTIF(kind='subscription') subscribe FROM ${table(dataset, "requests")} WHERE product_id IN UNNEST(@ids) GROUP BY product_id) r ON r.product_id=p LEFT JOIN (SELECT product_id,AVG(rating) average,COUNT(*) count FROM ${table(dataset, "feedback")} WHERE product_id IN UNNEST(@ids) GROUP BY product_id) f ON f.product_id=p`, { ids }, { ids: ["STRING"] }),
    close: () => undefined,
  };
}
