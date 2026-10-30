import { metadataPatch, overlay } from "../catalogue-record.js";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { derivedSchemaVersion, seedUsers, tables } from "./schema.js";

const parse = (value, fallback) => { try { return JSON.parse(value); } catch { return fallback; } };
const rows = (statement, ...values) => statement.all(...values);

export function openSqliteRepository(env) {
  const filename = env.PORTAL_SQLITE_PATH || "build/portal/portal.db";
  if (filename !== ":memory:") mkdirSync(dirname(filename), { recursive: true });
  const db = new DatabaseSync(filename);
  db.exec("PRAGMA foreign_keys = ON");
  const derived = {
    products: ["id", "unit_id", "table_name", "name", "description", "product_owner", "business_owner", "badges", "quality_score", "last_refresh", "ingested_at", "subscribe_count", "access_count", "view_count", "baseline_rating", "domain", "community", "application", "product_type", "record_count", "path", "data_format", "storage", "last_dq_run"],
    fields: ["product_id", "ordinal", "name", "business_name", "data_type", "description", "terms"],
    lineage: ["product_id", "direction", "ordinal", "value"],
    quality_rules: ["product_id", "field_name", "dimension", "rule_name", "rule_type", "fail_percent"],
    catalogue_sync: ["singleton", "release_id", "synced_at"],
  };
  db.exec("CREATE TABLE IF NOT EXISTS _portal_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1), derived_version INTEGER NOT NULL)");
  const shapeChanged = Object.entries(derived).some(([name, expected]) => {
    const actual = db.prepare(`PRAGMA table_info(_portal_${name})`).all().map((column) => column.name);
    return actual.length && (actual.length !== expected.length || actual.some((name, index) => name !== expected[index]));
  });
  if (shapeChanged) { db.close(); throw new Error("Portal record schema needs a migration; existing records were preserved."); }
  db.exec(`
    CREATE TABLE IF NOT EXISTS _portal_governed (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_products (id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, table_name TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, product_owner TEXT NOT NULL, business_owner TEXT NOT NULL, badges TEXT NOT NULL, quality_score REAL NOT NULL, last_refresh TEXT NOT NULL, ingested_at TEXT NOT NULL, subscribe_count INTEGER NOT NULL, access_count INTEGER NOT NULL, view_count INTEGER NOT NULL, baseline_rating REAL NOT NULL, domain TEXT NOT NULL, community TEXT NOT NULL, application TEXT NOT NULL, product_type TEXT NOT NULL, record_count INTEGER NOT NULL, path TEXT NOT NULL, data_format TEXT NOT NULL, storage TEXT NOT NULL, last_dq_run TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_fields (product_id TEXT NOT NULL, ordinal INTEGER NOT NULL, name TEXT NOT NULL, business_name TEXT NOT NULL, data_type TEXT NOT NULL, description TEXT NOT NULL, terms TEXT NOT NULL, PRIMARY KEY(product_id, name));
    CREATE TABLE IF NOT EXISTS _portal_lineage (product_id TEXT NOT NULL, direction TEXT NOT NULL, ordinal INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY(product_id, direction, ordinal));
    CREATE TABLE IF NOT EXISTS _portal_quality_rules (product_id TEXT NOT NULL, field_name TEXT NOT NULL, dimension TEXT NOT NULL, rule_name TEXT NOT NULL, rule_type TEXT NOT NULL, fail_percent REAL NOT NULL, PRIMARY KEY(product_id, field_name));
    CREATE TABLE IF NOT EXISTS _portal_catalogue_sync (singleton INTEGER PRIMARY KEY CHECK(singleton=1), release_id TEXT NOT NULL, synced_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_users (principal_id TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'viewer');
    CREATE TABLE IF NOT EXISTS _portal_favourites (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(principal_id, product_id));
    CREATE TABLE IF NOT EXISTS _portal_views (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, viewed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_cart (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(principal_id, product_id));
    CREATE TABLE IF NOT EXISTS _portal_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, product_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, product_id TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_ask_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, session_id INTEGER NOT NULL, question_id INTEGER NOT NULL, scope TEXT NOT NULL, direction TEXT NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_publish_status (product_id TEXT PRIMARY KEY, published INTEGER NOT NULL);
  `);
  db.prepare("INSERT INTO _portal_schema VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET derived_version=excluded.derived_version").run(derivedSchemaVersion);
  const requestColumns = new Set(db.prepare("PRAGMA table_info(_portal_requests)").all().map((column) => column.name));
  if (!requestColumns.has("decided_by")) db.exec("ALTER TABLE _portal_requests ADD COLUMN decided_by TEXT");
  if (!requestColumns.has("decided_at")) db.exec("ALTER TABLE _portal_requests ADD COLUMN decided_at TEXT");
  const insertRole = db.prepare("INSERT OR IGNORE INTO _portal_users VALUES (?, ?)");
  for (const user of seedUsers) insertRole.run(...user);

  const fieldRows = (id) => rows(db.prepare("SELECT f.*,q.dimension,q.rule_name,q.rule_type,q.fail_percent FROM _portal_fields f JOIN _portal_quality_rules q ON q.product_id=f.product_id AND q.field_name=f.name WHERE f.product_id=? ORDER BY f.ordinal"), id).map((field) => ({ name: field.name, business_name: field.business_name, data_type: field.data_type, description: field.description, terms: parse(field.terms, []), quality: { dimension: field.dimension, rule: field.rule_name, rule_type: field.rule_type, fail_percent: field.fail_percent } }));
  const lineageRows = (id) => {
    const value = { origin_paths: [], destination_paths: [], upstream: [], downstream: [] };
    for (const row of rows(db.prepare("SELECT direction,value FROM _portal_lineage WHERE product_id=? ORDER BY direction,ordinal"), id)) value[row.direction].push(row.value);
    return value;
  };
  const hydrate = (row) => ({ id: row.id, unit_id: row.unit_id, table: row.table_name, name: row.name, description: row.description, product_owner: row.product_owner, business_owner: row.business_owner, badges: parse(row.badges, []), quality_score: row.quality_score, last_refresh: row.last_refresh, ingested_at: row.ingested_at, counts: { subscribe: row.subscribe_count, access: row.access_count, views: row.view_count }, rating: row.baseline_rating, domain: row.domain, community: row.community, application: row.application, product_type: row.product_type, records: row.record_count, path: row.path, data_format: row.data_format, storage: row.storage, last_dq_run: row.last_dq_run, fields: fieldRows(row.id), lineage: lineageRows(row.id) });
  const productRows = (units, search = "") => {
    if (!units.length) return [];
    const marks = units.map(() => "?").join(",");
    const needle = `%${search.toLowerCase()}%`;
    const products = rows(db.prepare(`SELECT * FROM _portal_products WHERE unit_id IN (${marks}) AND (?='' OR lower(name||' '||description||' '||domain||' '||community||' '||application) LIKE ?) ORDER BY name`), ...units, search, needle).map(hydrate);
    const visible = new Set(rows(db.prepare(`SELECT id FROM _portal_products WHERE unit_id IN (${marks})`), ...units).map((product) => product.id));
    const current = parse(db.prepare("SELECT payload FROM _portal_governed WHERE singleton=1").get()?.payload, null);
    const active = current ? new Set((current.products || []).map((p) => `${p.unit_id}:${p.id}`)) : null;
    const governed = new Map((current?.products || []).map((p) => [p.id, p]));
    const live = active ? new Set([...governed.values()].filter((p) => visible.has(p.id) && units.includes(p.unit_id)).map((p) => p.id)) : visible;
    return products.filter((p) => !active || active.has(`${p.unit_id}:${p.id}`)).map((p) => overlay(p, governed.get(p.id), live)).map((product) => ({ ...product, lineage: { ...product.lineage, upstream: product.lineage.upstream.filter((id) => visible.has(id)), downstream: product.lineage.downstream.filter((id) => visible.has(id)) } }));
  };
  const syncCatalogue = (release, products, seedOnly = false) => {
    if (db.prepare("SELECT release_id FROM _portal_catalogue_sync WHERE singleton=1").get()?.release_id === release) return false;
    db.exec("BEGIN IMMEDIATE");
    try {
      db.prepare("INSERT INTO _portal_governed VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload").run(JSON.stringify({ release, products: products || [] }));
      if (products === null) {
        db.prepare("INSERT INTO _portal_catalogue_sync VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,synced_at=excluded.synced_at").run(release || "unreleased", new Date().toISOString());
        db.exec("COMMIT");
        return true;
      }
      if (!seedOnly) for (const table of ["quality_rules", "fields", "lineage", "products"]) db.exec(`DELETE FROM _portal_${table}`);
      const insertProduct = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
      const insertField = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_fields VALUES (?,?,?,?,?,?,?)`);
      const insertQuality = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_quality_rules VALUES (?,?,?,?,?,?)`);
      const insertLineage = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_lineage VALUES (?,?,?,?)`);
      for (const p of products) {
        db.prepare("UPDATE _portal_products SET unit_id=?,table_name=? WHERE id=?").run(p.unit_id, p.table, p.id);
        insertProduct.run(p.id, p.unit_id, p.table, p.name, p.description, p.product_owner, p.business_owner, JSON.stringify(p.badges), p.quality_score, p.last_refresh, p.ingested_at, p.counts.subscribe, p.counts.access, p.counts.views, p.rating, p.domain, p.community, p.application, p.product_type, p.records, p.path, p.data_format, p.storage, p.last_dq_run);
        p.fields.forEach((f, i) => { insertField.run(p.id, i, f.name, f.business_name, f.data_type, f.description, JSON.stringify(f.terms || [])); insertQuality.run(p.id, f.name, f.quality.dimension, f.quality.rule, f.quality.rule_type, f.quality.fail_percent); });
        for (const direction of ["origin_paths", "destination_paths", "upstream", "downstream"]) p.lineage[direction].forEach((value, i) => insertLineage.run(p.id, direction, i, value));
      }
      db.prepare("INSERT INTO _portal_catalogue_sync VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,synced_at=excluded.synced_at").run(release || "unreleased", new Date().toISOString());
      db.exec("COMMIT");
      return true;
    } catch (error) { db.exec("ROLLBACK"); throw error; }
  };
  const owned = (table, order = "", columns = "t.product_id") => (principal, units) => {
    if (!units.length) return [];
    const marks = units.map(() => "?").join(",");
    return rows(db.prepare(`SELECT ${columns} FROM _portal_${table} t JOIN _portal_products p ON p.id=t.product_id AND p.unit_id IN (${marks}) WHERE t.principal_id=?${order}`), ...units, principal);
  };
  return {
    setGoverned: (catalogue) => db.prepare("INSERT INTO _portal_governed VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload").run(JSON.stringify(catalogue)),
    governed: () => parse(db.prepare("SELECT payload FROM _portal_governed WHERE singleton=1").get()?.payload, null),
    updateMetadata: (id, patch) => {
      metadataPatch(patch);
      db.exec("BEGIN IMMEDIATE");
      try {
        for (const [key, value] of Object.entries(patch)) {
          if (key === "fields") for (const field of value) {
            for (const [name, text] of Object.entries(field)) if (name !== "name") db.prepare(`UPDATE _portal_fields SET ${name}=? WHERE product_id=? AND name=?`).run(text, id, field.name);
          }
          else db.prepare(`UPDATE _portal_products SET ${key}=? WHERE id=?`).run(key === "badges" ? JSON.stringify(value) : value, id);
        }
        db.exec("COMMIT");
      } catch (error) { db.exec("ROLLBACK"); throw error; }
    },
    tableNames: () => tables.map((name) => `_portal_${name}`), syncCatalogue, catalogueRelease: () => db.prepare("SELECT release_id FROM _portal_catalogue_sync WHERE singleton=1").get()?.release_id,
    listProducts: (units, search = "") => productRows(units, search), product: (id, units) => productRows(units).find((p) => p.id === id), fields: (id, units) => productRows(units).find((p) => p.id === id)?.fields || [], lineage: (id, units) => productRows(units).find((p) => p.id === id)?.lineage || { origin_paths: [], destination_paths: [], upstream: [], downstream: [] },
    role: (principal) => db.prepare("SELECT role FROM _portal_users WHERE principal_id=?").get(principal)?.role || "viewer", setRole: (principal, role) => db.prepare("INSERT INTO _portal_users VALUES (?,?) ON CONFLICT(principal_id) DO UPDATE SET role=excluded.role").run(principal, role), users: () => rows(db.prepare("SELECT principal_id,role FROM _portal_users ORDER BY principal_id")),
    view: (principal, id) => db.prepare("INSERT INTO _portal_views VALUES (?,?,?)").run(principal, id, new Date().toISOString()), published: (id) => db.prepare("SELECT published FROM _portal_publish_status WHERE product_id=?").get(id)?.published, setPublished: (id, published) => db.prepare("INSERT INTO _portal_publish_status VALUES (?,?) ON CONFLICT(product_id) DO UPDATE SET published=excluded.published").run(id, published ? 1 : 0),
    favourites: owned("favourites"), addFavourite: (principal, id) => db.prepare("INSERT OR IGNORE INTO _portal_favourites VALUES (?,?)").run(principal, id), removeFavourite: (principal, id) => db.prepare("DELETE FROM _portal_favourites WHERE principal_id=? AND product_id=?").run(principal, id),
    cart: owned("cart"), addCart: (principal, id) => db.prepare("INSERT OR IGNORE INTO _portal_cart VALUES (?,?)").run(principal, id), removeCart: (principal, id) => db.prepare("DELETE FROM _portal_cart WHERE principal_id=? AND product_id=?").run(principal, id), emptyCart: (principal) => db.prepare("DELETE FROM _portal_cart WHERE principal_id=?").run(principal),
    request: (principal, id, kind) => db.prepare("INSERT INTO _portal_requests(principal_id,product_id,kind,status,created_at) VALUES (?,?,?,'OPEN',?)").run(principal, id, kind, new Date().toISOString()), requests: owned("requests", " ORDER BY t.id DESC", "t.*"), queue: () => rows(db.prepare("SELECT * FROM _portal_requests WHERE status='OPEN' ORDER BY id")), pending: (id) => db.prepare("SELECT * FROM _portal_requests WHERE id=? AND status='OPEN'").get(String(id ?? "")), decide: (id, status, approver) => db.prepare("UPDATE _portal_requests SET status=?,decided_by=?,decided_at=? WHERE id=? AND status='OPEN'").run(status, approver, new Date().toISOString(), String(id)).changes,
    feedback: (principal, id, rating, comment) => db.prepare("INSERT INTO _portal_feedback(principal_id,product_id,rating,comment,created_at) VALUES (?,?,?,?,?)").run(principal, id, rating, comment, new Date().toISOString()), askFeedback: (principal, session, question, scope, direction, comment) => db.prepare("INSERT INTO _portal_ask_feedback(principal_id,session_id,question_id,scope,direction,comment,created_at) VALUES (?,?,?,?,?,?,?)").run(principal, session, question, JSON.stringify(scope), direction, comment, new Date().toISOString()), averageFeedback: (id) => db.prepare("SELECT avg(rating) average,count(*) count FROM _portal_feedback WHERE product_id=?").get(id), activity: (id) => ({ views: db.prepare("SELECT count(*) count FROM _portal_views WHERE product_id=?").get(id).count, access: db.prepare("SELECT count(*) count FROM _portal_requests WHERE product_id=? AND kind!='subscription'").get(id).count, subscribe: db.prepare("SELECT count(*) count FROM _portal_requests WHERE product_id=? AND kind='subscription'").get(id).count, feedback: db.prepare("SELECT avg(rating) average,count(*) count FROM _portal_feedback WHERE product_id=?").get(id) }), close: () => db.close(),
  };
}
