import { metadataPatch, overlay } from "../catalogue-record.js";
import { BigQuery } from "@google-cloud/bigquery";
import { derivedSchemaVersion, seedUsers, tables } from "./schema.js";

function table(dataset, name) {
  return `\`${dataset}.\_portal_${name}\``;
}

const schemas = {
  governed: "singleton INT64 NOT NULL, payload STRING NOT NULL",
  products: "id STRING NOT NULL, unit_id STRING NOT NULL, table_name STRING NOT NULL, name STRING NOT NULL, description STRING NOT NULL, product_owner STRING NOT NULL, business_owner STRING NOT NULL, badges STRING NOT NULL, quality_score FLOAT64 NOT NULL, last_refresh STRING NOT NULL, ingested_at STRING NOT NULL, subscribe_count INT64 NOT NULL, access_count INT64 NOT NULL, view_count INT64 NOT NULL, baseline_rating FLOAT64 NOT NULL, domain STRING NOT NULL, community STRING NOT NULL, application STRING NOT NULL, product_type STRING NOT NULL, record_count INT64 NOT NULL, path STRING NOT NULL, data_format STRING NOT NULL, storage STRING NOT NULL, last_dq_run STRING NOT NULL",
  fields: "product_id STRING NOT NULL, ordinal INT64 NOT NULL, name STRING NOT NULL, business_name STRING NOT NULL, data_type STRING NOT NULL, description STRING NOT NULL, terms STRING NOT NULL",
  lineage: "product_id STRING NOT NULL, direction STRING NOT NULL, ordinal INT64 NOT NULL, value STRING NOT NULL",
  quality_rules: "product_id STRING NOT NULL, field_name STRING NOT NULL, dimension STRING NOT NULL, rule_name STRING NOT NULL, rule_type STRING NOT NULL, fail_percent FLOAT64 NOT NULL",
  catalogue_sync: "singleton INT64 NOT NULL, release_id STRING NOT NULL, synced_at TIMESTAMP NOT NULL",
  users: "principal_id STRING NOT NULL, role STRING NOT NULL",
  favourites: "principal_id STRING NOT NULL, product_id STRING NOT NULL",
  views: "principal_id STRING NOT NULL, product_id STRING NOT NULL, viewed_at TIMESTAMP NOT NULL",
  cart: "principal_id STRING NOT NULL, product_id STRING NOT NULL",
  requests: "id STRING NOT NULL, principal_id STRING NOT NULL, product_id STRING NOT NULL, kind STRING NOT NULL, status STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  feedback: "id STRING NOT NULL, principal_id STRING NOT NULL, product_id STRING NOT NULL, rating INT64 NOT NULL, comment STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  ask_feedback: "id STRING NOT NULL, principal_id STRING NOT NULL, session_id INT64 NOT NULL, question_id INT64 NOT NULL, scope STRING NOT NULL, direction STRING NOT NULL, comment STRING NOT NULL, created_at TIMESTAMP NOT NULL",
  publish_status: "product_id STRING NOT NULL, published BOOL NOT NULL",
  schema: "singleton INT64 NOT NULL, derived_version INT64 NOT NULL",
};

// Each table's column types, read from the schema above, so a query parameter
// bound into a table is typed by the table's own columns.
const columnTypes = (name) => Object.fromEntries(schemas[name].split(", ").map((column) => column.split(" ").slice(0, 2)));

export function openBigQueryRepository(env, options = {}) {
  const client = options.client || new BigQuery({ projectId: env.BIGQUERY_PROJECT });
  if (!env.PROFILE) throw new Error("PROFILE is required for the BigQuery portal repository");
  const profile = env.PROFILE.replaceAll("-", "_");
  const dataset = `${env.BIGQUERY_DATASET_PREFIX || "kge"}_${profile}_warehouses`;
  const query = async (sql, params = {}, types) => (await client.query(types ? { query: sql, params, types } : { query: sql, params }))[0];
  const initialize = async () => {
    const derived = ["products", "fields", "lineage", "quality_rules", "catalogue_sync"];
    await query(`CREATE TABLE IF NOT EXISTS ${table(dataset, "schema")} (${schemas.schema})`);
    const expected = Object.fromEntries(derived.map((name) => [name, schemas[name].split(", ").map((column) => column.split(" ")[0])]));
    const held = await query(`SELECT table_name,column_name,ordinal_position FROM \`${dataset}.INFORMATION_SCHEMA.COLUMNS\` WHERE table_name IN UNNEST(@tables) ORDER BY table_name,ordinal_position`, { tables: derived.map((name) => `_portal_${name}`) }, { tables: ["STRING"] });
    const actual = new Map();
    for (const column of held) {
      const name = column.table_name.replace(/^_portal_/, "");
      actual.set(name, [...(actual.get(name) || []), column.column_name]);
    }
    const shapeChanged = derived.some((name) => actual.has(name) && (actual.get(name).length !== expected[name].length || actual.get(name).some((column, index) => column !== expected[name][index])));
    if (shapeChanged) throw new Error("Portal record schema needs a migration; existing records were preserved.");
    for (const name of tables.filter((name) => name !== "schema")) await query(`CREATE TABLE IF NOT EXISTS ${table(dataset, name)} (${schemas[name]})`);
    await query(`MERGE ${table(dataset, "schema")} t USING (SELECT 1 singleton,@version derived_version) s ON t.singleton=s.singleton WHEN MATCHED THEN UPDATE SET derived_version=s.derived_version WHEN NOT MATCHED THEN INSERT ROW`, { version: derivedSchemaVersion });
    await query(`ALTER TABLE ${table(dataset, "requests")} ADD COLUMN IF NOT EXISTS decided_by STRING, ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP`);
    await query(`MERGE ${table(dataset, "users")} t USING (SELECT * FROM UNNEST(@rows)) s ON t.principal_id=s.principal_id WHEN NOT MATCHED THEN INSERT ROW`, { rows: seedUsers.map(([principal_id, role]) => ({ principal_id, role })) });
  };
  let ready;
  const run = async (sql, params, types) => {
    ready ??= initialize().catch((error) => { ready = undefined; throw error; });
    await ready;
    return query(sql, params, types);
  };
  const owned = (name, order = "", columns = "t.product_id") => (principal, units) => run(`SELECT ${columns} FROM ${table(dataset, name)} t JOIN ${table(dataset, "products")} p ON p.id=t.product_id AND p.unit_id IN UNNEST(@units) WHERE t.principal_id=@principal${order}`, { principal, units }, { units: ["STRING"] });
  const fields = async (id) => {
    const values = await run(`SELECT f.*,q.dimension,q.rule_name,q.rule_type,q.fail_percent FROM ${table(dataset, "fields")} f JOIN ${table(dataset, "quality_rules")} q ON q.product_id=f.product_id AND q.field_name=f.name WHERE f.product_id=@id ORDER BY f.ordinal`, { id });
    return values.map((field) => ({ name: field.name, business_name: field.business_name, data_type: field.data_type, description: field.description, terms: JSON.parse(field.terms || "[]"), quality: { dimension: field.dimension, rule: field.rule_name, rule_type: field.rule_type, fail_percent: Number(field.fail_percent) } }));
  };
  const lineage = async (id) => {
    const value = { origin_paths: [], destination_paths: [], upstream: [], downstream: [] };
    for (const row of await run(`SELECT direction,value FROM ${table(dataset, "lineage")} WHERE product_id=@id ORDER BY direction,ordinal`, { id })) value[row.direction].push(row.value);
    return value;
  };
  const listProducts = async (units, search = "") => {
    const found = await run(`SELECT * FROM ${table(dataset, "products")} WHERE unit_id IN UNNEST(@units) AND (@search='' OR STRPOS(LOWER(CONCAT(name,' ',description,' ',domain,' ',community,' ',application)),LOWER(@search))>0) ORDER BY name`, { units, search }, { units: ["STRING"] });
    const products = await Promise.all(found.map(async (row) => ({ id: row.id, unit_id: row.unit_id, table: row.table_name, name: row.name, description: row.description, product_owner: row.product_owner, business_owner: row.business_owner, badges: JSON.parse(row.badges || "[]"), quality_score: Number(row.quality_score), last_refresh: row.last_refresh, ingested_at: row.ingested_at, counts: { subscribe: Number(row.subscribe_count), access: Number(row.access_count), views: Number(row.view_count) }, rating: Number(row.baseline_rating), domain: row.domain, community: row.community, application: row.application, product_type: row.product_type, records: Number(row.record_count), path: row.path, data_format: row.data_format, storage: row.storage, last_dq_run: row.last_dq_run, fields: await fields(row.id), lineage: await lineage(row.id) })));
    const readable = await run(`SELECT id FROM ${table(dataset, "products")} WHERE unit_id IN UNNEST(@units)`, { units }, { units: ["STRING"] });
    const visible = new Set(readable.map((product) => product.id));
    const held = (await run(`SELECT payload FROM ${table(dataset, "governed")} WHERE singleton=1 LIMIT 1`))[0];
    const active = held ? new Set((JSON.parse(held.payload).products || []).map((p) => `${p.unit_id}:${p.id}`)) : null;
    const governed = new Map((held ? JSON.parse(held.payload).products || [] : []).map((p) => [p.id, p]));
    const live = active ? new Set([...governed.values()].filter((p) => visible.has(p.id) && units.includes(p.unit_id)).map((p) => p.id)) : visible;
    return products.filter((p) => !active || active.has(`${p.unit_id}:${p.id}`)).map((p) => overlay(p, governed.get(p.id), live)).map((product) => ({ ...product, lineage: { ...product.lineage, upstream: product.lineage.upstream.filter((id) => visible.has(id)), downstream: product.lineage.downstream.filter((id) => visible.has(id)) } }));
  };
  const syncCatalogue = async (release, products, seedOnly = false) => {
    const held = (await run(`SELECT release_id FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1 LIMIT 1`))[0]?.release_id;
    if (held === release) return false;
    const catalogue = JSON.stringify({ release, products: products || [] });
    // Every parameter carries its column's type: left to infer, the client
    // types an array of structs from its first row alone, so a first
    // quality_score of 0 would make the whole column INT64 (#159).
    const heldTypes = { catalogue: columnTypes("governed").payload, release: columnTypes("catalogue_sync").release_id };
    if (products === null) {
      await run(`BEGIN TRANSACTION; DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue); DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;`, { catalogue, release: release || "unreleased" }, heldTypes);
      return true;
    }
    const productRows = products.map((p) => ({ id: p.id, unit_id: p.unit_id, table_name: p.table, name: p.name, description: p.description, product_owner: p.product_owner, business_owner: p.business_owner, badges: JSON.stringify(p.badges), quality_score: p.quality_score, last_refresh: p.last_refresh, ingested_at: p.ingested_at, subscribe_count: p.counts.subscribe, access_count: p.counts.access, view_count: p.counts.views, baseline_rating: p.rating, domain: p.domain, community: p.community, application: p.application, product_type: p.product_type, record_count: p.records, path: p.path, data_format: p.data_format, storage: p.storage, last_dq_run: p.last_dq_run }));
    const fieldRows = products.flatMap((p) => p.fields.map((f, ordinal) => ({ product_id: p.id, ordinal, name: f.name, business_name: f.business_name, data_type: f.data_type, description: f.description, terms: JSON.stringify(f.terms || []) })));
    const qualityRows = products.flatMap((p) => p.fields.map((f) => ({ product_id: p.id, field_name: f.name, dimension: f.quality.dimension, rule_name: f.quality.rule, rule_type: f.quality.rule_type, fail_percent: f.quality.fail_percent })));
    const lineageRows = products.flatMap((p) => ["origin_paths", "destination_paths", "upstream", "downstream"].flatMap((direction) => p.lineage[direction].map((value, ordinal) => ({ product_id: p.id, direction, ordinal, value }))));
    if (!products.length && seedOnly) return syncCatalogue(release, null);
    if (!products.length) {
      await run(`BEGIN TRANSACTION; DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue); DELETE FROM ${table(dataset, "quality_rules")} WHERE TRUE; DELETE FROM ${table(dataset, "fields")} WHERE TRUE; DELETE FROM ${table(dataset, "lineage")} WHERE TRUE; DELETE FROM ${table(dataset, "products")} WHERE TRUE; DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;`, { catalogue, release: release || "unreleased" }, heldTypes);
      return true;
    }
    const insert = (name, parameter, key) => seedOnly
      ? `MERGE ${table(dataset, name)} t USING (SELECT * FROM UNNEST(@${parameter})) s ON ${key} ${name === "products" ? "WHEN MATCHED THEN UPDATE SET unit_id=s.unit_id,table_name=s.table_name " : ""}WHEN NOT MATCHED THEN INSERT ROW;`
      : `INSERT ${table(dataset, name)} SELECT * FROM UNNEST(@${parameter});`;
    await run(`BEGIN TRANSACTION; DELETE FROM ${table(dataset, "governed")} WHERE singleton=1; INSERT ${table(dataset, "governed")} VALUES (1,@catalogue);
${seedOnly ? "" : `DELETE FROM ${table(dataset, "quality_rules")} WHERE TRUE; DELETE FROM ${table(dataset, "fields")} WHERE TRUE; DELETE FROM ${table(dataset, "lineage")} WHERE TRUE; DELETE FROM ${table(dataset, "products")} WHERE TRUE;`}
${insert("products", "products", "t.id=s.id")}
${fieldRows.length ? insert("fields", "fields", "t.product_id=s.product_id AND t.name=s.name") + insert("quality_rules", "quality", "t.product_id=s.product_id AND t.field_name=s.field_name") : ""}
${lineageRows.length ? insert("lineage", "lineage", "t.product_id=s.product_id AND t.direction=s.direction AND t.ordinal=s.ordinal") : ""}
DELETE FROM ${table(dataset, "catalogue_sync")} WHERE singleton=1; INSERT ${table(dataset, "catalogue_sync")} VALUES (1,@release,CURRENT_TIMESTAMP()); COMMIT TRANSACTION;`, { products: productRows, ...(fieldRows.length ? { fields: fieldRows, quality: qualityRows } : {}), ...(lineageRows.length ? { lineage: lineageRows } : {}), catalogue, release: release || "unreleased" },
    { products: [columnTypes("products")], ...(fieldRows.length ? { fields: [columnTypes("fields")], quality: [columnTypes("quality_rules")] } : {}), ...(lineageRows.length ? { lineage: [columnTypes("lineage")] } : {}), ...heldTypes });
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
    listProducts, product: async (id, units) => (await listProducts(units)).find((p) => p.id === id), fields: async (id, units) => (await listProducts(units)).find((p) => p.id === id)?.fields || [], lineage: async (id, units) => (await listProducts(units)).find((p) => p.id === id)?.lineage || { origin_paths: [], destination_paths: [], upstream: [], downstream: [] },
    role: async (principal) => (await run(`SELECT role FROM ${table(dataset, "users")} WHERE principal_id=@principal LIMIT 1`, { principal }))[0]?.role || "viewer",
    setRole: (principal, role) => run(`MERGE ${table(dataset, "users")} t USING (SELECT @principal principal_id,@role role) s ON t.principal_id=s.principal_id WHEN MATCHED THEN UPDATE SET role=s.role WHEN NOT MATCHED THEN INSERT ROW`, { principal, role }),
    view: (principal, id) => run(`INSERT ${table(dataset, "views")} VALUES (@principal,@id,CURRENT_TIMESTAMP())`, { principal, id }),
    users: () => run(`SELECT principal_id,role FROM ${table(dataset, "users")} ORDER BY principal_id`),
    published: async (id) => (await run(`SELECT published FROM ${table(dataset, "publish_status")} WHERE product_id=@id LIMIT 1`, { id }))[0]?.published,
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
    close: () => undefined,
  };
}
