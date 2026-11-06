import { followCatalogue, heldCards, metadataPatch, overlay, qualityRules, settleCards } from "../catalogue-record.js";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { normalizeUsername } from "../portal-access.js";
import { checkedActor, checkedRole, derivedSchemaVersion, explicitRoles, formerlySeededGrants, migrationEntries, primaryRole, seededGrantsRemoval, tables } from "./schema.js";

const parse = (value, fallback) => { try { return JSON.parse(value); } catch { return fallback; } };
const rows = (statement, ...values) => statement.all(...values);

export function openSqliteRepository(env) {
  const filename = env.PORTAL_SQLITE_PATH || "build/portal/portal.db";
  if (filename !== ":memory:") mkdirSync(dirname(filename), { recursive: true });
  const db = new DatabaseSync(filename);
  db.exec("PRAGMA foreign_keys = ON");
  const derived = {
    products: ["id", "unit_id", "table_name", "name", "description", "product_owner", "business_owner", "badges", "quality_score", "last_refresh", "ingested_at", "subscribe_count", "access_count", "view_count", "baseline_rating", "domain", "community", "application", "product_type", "record_count", "path", "data_format", "storage", "last_dq_run", "asset_type", "usage_purpose", "dataset_type", "APM_ID", "project", "product_owner_email", "business_owner_email", "refresh_cadence", "schema"],
    fields: ["product_id", "ordinal", "name", "business_name", "data_type", "description", "terms", "classification", "pii"],
    lineage: ["product_id", "direction", "ordinal", "value"],
    quality_rules: ["product_id", "field_name", "dimension", "rule_name", "rule_type", "fail_percent", "description", "rule_ordinal"],
    catalogue_sync: ["singleton", "release_id", "synced_at"],
  };
  const addedColumns = { products: ["asset_type", "usage_purpose", "dataset_type", "APM_ID", "project", "product_owner_email", "business_owner_email", "refresh_cadence", "schema"], fields: ["classification", "pii"], quality_rules: ["description", "rule_ordinal"] };
  const integerColumns = ["pii", "rule_ordinal"];
  const qualityRulesTable = "product_id TEXT NOT NULL, field_name TEXT NOT NULL, dimension TEXT NOT NULL, rule_name TEXT NOT NULL, rule_type TEXT NOT NULL, fail_percent REAL, description TEXT NOT NULL DEFAULT '', rule_ordinal INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(product_id, field_name, rule_ordinal)";
  db.exec("CREATE TABLE IF NOT EXISTS _portal_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1), derived_version INTEGER NOT NULL)");
  const shapeChanged = Object.entries(derived).some(([name, expected]) => {
    const actual = db.prepare(`PRAGMA table_info(_portal_${name})`).all().map((column) => column.name);
    const compatible = expected.filter((column) => !(addedColumns[name] || []).includes(column) || actual.includes(column));
    return actual.length && (actual.length !== compatible.length || actual.some((name, index) => name !== compatible[index]));
  });
  if (shapeChanged) { db.close(); throw new Error("Portal record schema needs a migration; existing records were preserved."); }
  db.exec(`
    CREATE TABLE IF NOT EXISTS _portal_governed (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_products (id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, table_name TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, product_owner TEXT NOT NULL, business_owner TEXT NOT NULL, badges TEXT NOT NULL, quality_score REAL NOT NULL, last_refresh TEXT NOT NULL, ingested_at TEXT NOT NULL, subscribe_count INTEGER NOT NULL, access_count INTEGER NOT NULL, view_count INTEGER NOT NULL, baseline_rating REAL NOT NULL, domain TEXT NOT NULL, community TEXT NOT NULL, application TEXT NOT NULL, product_type TEXT NOT NULL, record_count INTEGER NOT NULL, path TEXT NOT NULL, data_format TEXT NOT NULL, storage TEXT NOT NULL, last_dq_run TEXT NOT NULL, asset_type TEXT NOT NULL DEFAULT '', usage_purpose TEXT NOT NULL DEFAULT '', dataset_type TEXT NOT NULL DEFAULT '', APM_ID TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '', product_owner_email TEXT NOT NULL DEFAULT '', business_owner_email TEXT NOT NULL DEFAULT '', refresh_cadence TEXT NOT NULL DEFAULT '', schema TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS _portal_fields (product_id TEXT NOT NULL, ordinal INTEGER NOT NULL, name TEXT NOT NULL, business_name TEXT NOT NULL, data_type TEXT NOT NULL, description TEXT NOT NULL, terms TEXT NOT NULL, classification TEXT NOT NULL DEFAULT '', pii INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(product_id, name));
    CREATE TABLE IF NOT EXISTS _portal_lineage (product_id TEXT NOT NULL, direction TEXT NOT NULL, ordinal INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY(product_id, direction, ordinal));
    CREATE TABLE IF NOT EXISTS _portal_quality_rules (${qualityRulesTable});
    CREATE TABLE IF NOT EXISTS _portal_catalogue_sync (singleton INTEGER PRIMARY KEY CHECK(singleton=1), release_id TEXT NOT NULL, synced_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_users (principal_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(principal_id, role));
    CREATE TABLE IF NOT EXISTS _portal_role_audit (id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, principal_id TEXT NOT NULL, before_roles TEXT NOT NULL, after_roles TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_identity_migrations (principal_id TEXT PRIMARY KEY, username TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_upgrades (name TEXT PRIMARY KEY, at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_favourites (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(principal_id, product_id));
    CREATE TABLE IF NOT EXISTS _portal_views (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, viewed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_cart (principal_id TEXT NOT NULL, product_id TEXT NOT NULL, PRIMARY KEY(principal_id, product_id));
    CREATE TABLE IF NOT EXISTS _portal_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, product_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, product_id TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_ask_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL, session_id INTEGER NOT NULL, question_id INTEGER NOT NULL, scope TEXT NOT NULL, direction TEXT NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS _portal_publish_status (product_id TEXT PRIMARY KEY, published INTEGER NOT NULL);
  `);
  for (const [name, columns] of Object.entries(addedColumns)) {
    const held = new Set(db.prepare(`PRAGMA table_info(_portal_${name})`).all().map((column) => column.name));
    for (const column of columns) if (!held.has(column)) db.exec(`ALTER TABLE _portal_${name} ADD COLUMN ${column} ${integerColumns.includes(column) ? 'INTEGER NOT NULL DEFAULT 0' : "TEXT NOT NULL DEFAULT ''"}`);
  }
  // Several rules a column and a blank fail %: the key gains the rule's
  // ordinal and fail_percent takes NULL, so an older table is rebuilt with its
  // rows kept as each column's first rule.
  if (!db.prepare("PRAGMA table_info(_portal_quality_rules)").all().find((column) => column.name === "rule_ordinal").pk) {
    db.exec(`BEGIN IMMEDIATE;
      ALTER TABLE _portal_quality_rules RENAME TO _portal_quality_rules_single;
      CREATE TABLE _portal_quality_rules (${qualityRulesTable});
      INSERT INTO _portal_quality_rules SELECT ${derived.quality_rules.join(",")} FROM _portal_quality_rules_single;
      DROP TABLE _portal_quality_rules_single;
      COMMIT;`);
  }
  db.prepare("INSERT INTO _portal_schema VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET derived_version=excluded.derived_version").run(derivedSchemaVersion);
  const requestColumns = new Set(db.prepare("PRAGMA table_info(_portal_requests)").all().map((column) => column.name));
  if (!requestColumns.has("decided_by")) db.exec("ALTER TABLE _portal_requests ADD COLUMN decided_by TEXT");
  if (!requestColumns.has("decided_at")) db.exec("ALTER TABLE _portal_requests ADD COLUMN decided_at TEXT");
  if (!db.prepare("PRAGMA table_info(_portal_users)").all().find((column) => column.name === "role").pk) {
    db.exec(`BEGIN IMMEDIATE;
      ALTER TABLE _portal_users RENAME TO _portal_users_single;
      CREATE TABLE _portal_users (principal_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(principal_id, role));
      INSERT INTO _portal_users SELECT principal_id,role FROM _portal_users_single;
      DROP TABLE _portal_users_single;
      COMMIT;`);
  }

  const transaction = (action) => {
    db.exec("BEGIN IMMEDIATE");
    try { const result = action(); db.exec("COMMIT"); return result; }
    catch (error) { db.exec("ROLLBACK"); throw error; }
  };
  const grants = (principal) => explicitRoles(db.prepare("SELECT role FROM _portal_users WHERE principal_id=? ORDER BY role").all(principal).map(({ role }) => role));
  const roles = (principal) => { const held = grants(principal); return held.length ? held : ["viewer"]; };
  const saveAudit = (principal, before, after, by) => {
    const entry = { id: randomUUID(), at: new Date().toISOString(), by, principal, before, after };
    db.prepare("INSERT INTO _portal_role_audit VALUES (?,?,?,?,?,?)").run(entry.id, entry.at, by, principal, JSON.stringify(before), JSON.stringify(after));
    return entry;
  };
  const changeRole = (principal, role, grant, by) => {
    principal = normalizeUsername(principal); checkedActor(by); checkedRole(role);
    if (typeof grant !== "boolean") throw new Error("A grant or removal is required");
    return transaction(() => {
      const before = grants(principal);
      if (grant) db.prepare("INSERT OR IGNORE INTO _portal_users VALUES (?,?)").run(principal, role);
      else db.prepare("DELETE FROM _portal_users WHERE principal_id=? AND role=?").run(principal, role);
      return saveAudit(principal, before, grants(principal), by);
    });
  };
  transaction(() => {
    if (db.prepare("SELECT 1 FROM _portal_upgrades WHERE name=?").get(seededGrantsRemoval)) return;
    for (const { principal, role } of formerlySeededGrants) {
      if (!db.prepare("SELECT 1 FROM _portal_users WHERE principal_id=? AND role=?").get(principal, role)) continue;
      const before = db.prepare("SELECT role FROM _portal_users WHERE principal_id=? ORDER BY role").all(principal).map((row) => row.role);
      db.prepare("DELETE FROM _portal_users WHERE principal_id=? AND role=?").run(principal, role);
      saveAudit(principal, before, before.filter((held) => held !== role), "upgrade");
    }
    db.prepare("INSERT INTO _portal_upgrades VALUES (?,?)").run(seededGrantsRemoval, new Date().toISOString());
  });
  const migrateIdentities = (map, by = "migration") => {
    const entries = migrationEntries(map); by = normalizeUsername(by);
    return transaction(() => {
      const report = { migrated: 0, skipped: 0, rows: {} };
      for (const { principal, username } of entries) {
        const previous = db.prepare("SELECT username FROM _portal_identity_migrations WHERE principal_id=?").get(principal);
        if (previous && previous.username !== username) throw new Error("An identity was already migrated to another username");
        if (previous) { report.skipped++; continue; }
        if (principal !== username && db.prepare("SELECT 1 FROM _portal_identity_migrations WHERE username=? AND principal_id<>?").get(principal, principal)) throw new Error("Identity migration chains are ambiguous");
        if (db.prepare("SELECT 1 FROM _portal_identity_migrations WHERE principal_id=? AND username<>?").get(username, username)) throw new Error("Identity migration chains are ambiguous");
        const count = (name) => { report.rows[name] = (report.rows[name] || 0) + Number(db.prepare(`SELECT count(*) AS count FROM _portal_${name} WHERE principal_id=?`).get(principal).count); };
        if (principal !== username) {
          for (const [name, column] of [["users", "role"], ["favourites", "product_id"], ["cart", "product_id"]]) {
            count(name);
            db.prepare(`INSERT OR IGNORE INTO _portal_${name} SELECT ?,${column} FROM _portal_${name} WHERE principal_id=?`).run(username, principal);
            db.prepare(`DELETE FROM _portal_${name} WHERE principal_id=?`).run(principal);
          }
          for (const name of ["views", "requests", "feedback", "ask_feedback"]) {
            count(name);
            db.prepare(`UPDATE _portal_${name} SET principal_id=? WHERE principal_id=?`).run(username, principal);
          }
          report.rows.decisions = (report.rows.decisions || 0) + Number(db.prepare("UPDATE _portal_requests SET decided_by=? WHERE decided_by=?").run(username, principal).changes);
          db.prepare("UPDATE _portal_role_audit SET principal_id=? WHERE principal_id=?").run(username, principal);
          db.prepare("UPDATE _portal_role_audit SET actor=? WHERE actor=?").run(username, principal);
        }
        db.prepare("INSERT INTO _portal_identity_migrations VALUES (?,?,?,?)").run(principal, username, new Date().toISOString(), by);
        report.migrated++;
      }
      return report;
    });
  };

  const emptyLineage = () => ({ origin_paths: [], destination_paths: [], upstream: [], downstream: [] });
  const hydrate = (row, fieldRows, lineageRows) => ({ id: row.id, unit_id: row.unit_id, table: row.table_name, name: row.name, description: row.description, product_owner: row.product_owner, business_owner: row.business_owner, badges: parse(row.badges, []), quality_score: row.quality_score, last_refresh: row.last_refresh, ingested_at: row.ingested_at, counts: { subscribe: row.subscribe_count, access: row.access_count, views: row.view_count }, rating: row.baseline_rating, domain: row.domain, community: row.community, application: row.application, product_type: row.product_type, records: row.record_count, path: row.path, data_format: row.data_format, storage: row.storage, last_dq_run: row.last_dq_run, asset_type: row.asset_type, usage_purpose: row.usage_purpose, dataset_type: row.dataset_type, APM_ID: row.APM_ID, project: row.project, product_owner_email: row.product_owner_email, business_owner_email: row.business_owner_email, refresh_cadence: row.refresh_cadence, schema: row.schema, fields: fieldRows.get(row.id) || [], lineage: lineageRows.get(row.id) || emptyLineage() });
  const productRows = (units, search = "", selection) => {
    if (!units.length) return [];
    const marks = units.map(() => "?").join(",");
    const needle = `%${search.toLowerCase()}%`;
    const found = rows(db.prepare(`SELECT *,
(SELECT json_group_array(id) FROM _portal_products WHERE unit_id IN (${marks})) visible_ids,
(SELECT payload FROM _portal_governed WHERE singleton=1) governed_payload
FROM _portal_products WHERE unit_id IN (${marks}) AND (?='' OR lower(name||' '||description||' '||domain||' '||community||' '||application) LIKE ?)${selection ? " AND id=?" : ""} ORDER BY name`), ...units, ...units, search, needle, ...(selection ? [selection.id ?? null] : []));
    const ids = found.map((row) => row.id), idMarks = ids.map(() => "?").join(",");
    const fields = rows(db.prepare(`SELECT f.*,q.dimension,q.rule_name,q.rule_type,q.fail_percent,q.description AS rule_description FROM _portal_fields f JOIN _portal_quality_rules q ON q.product_id=f.product_id AND q.field_name=f.name WHERE f.product_id IN (${idMarks}) ORDER BY f.product_id,f.ordinal,q.rule_ordinal`), ...ids);
    const lineage = rows(db.prepare(`SELECT product_id,direction,value FROM _portal_lineage WHERE product_id IN (${idMarks}) ORDER BY product_id,direction,ordinal`), ...ids);
    const fieldRows = new Map(), lineageRows = new Map();
    for (const field of fields) {
      if (!fieldRows.has(field.product_id)) fieldRows.set(field.product_id, []);
      const held = fieldRows.get(field.product_id), rule = { dimension: field.dimension, rule: field.rule_name, rule_type: field.rule_type, fail_percent: field.fail_percent, description: field.rule_description || "" };
      if (held.at(-1)?.name === field.name) held.at(-1).quality = [...qualityRules(held.at(-1).quality), rule];
      else held.push({ name: field.name, business_name: field.business_name, data_type: field.data_type, description: field.description, terms: parse(field.terms, []), classification: field.classification, pii: field.pii === 1, quality: rule });
    }
    for (const row of lineage) {
      if (!lineageRows.has(row.product_id)) lineageRows.set(row.product_id, emptyLineage());
      lineageRows.get(row.product_id)[row.direction].push(row.value);
    }
    const products = found.map((row) => hydrate(row, fieldRows, lineageRows));
    const visible = new Set(parse(found[0]?.visible_ids, []));
    const current = parse(found[0]?.governed_payload, null);
    const active = current ? new Set((current.products || []).map((p) => `${p.unit_id}:${p.id}`)) : null;
    const governed = new Map((current?.products || []).map((p) => [p.id, p]));
    const live = active ? new Set([...governed.values()].filter((p) => visible.has(p.id) && units.includes(p.unit_id)).map((p) => p.id)) : visible;
    return products.filter((p) => !active || active.has(`${p.unit_id}:${p.id}`)).map((p) => overlay(p, governed.get(p.id), live)).map((product) => ({ ...product, lineage: { ...product.lineage, upstream: product.lineage.upstream.filter((id) => visible.has(id)), downstream: product.lineage.downstream.filter((id) => visible.has(id)) } }));
  };
  const syncCatalogue = (release, products, mode = false) => {
    const seedOnly = Boolean(mode);
    if (db.prepare("SELECT release_id FROM _portal_catalogue_sync WHERE singleton=1").get()?.release_id === release) return false;
    db.exec("BEGIN IMMEDIATE");
    try {
      const payload = parse(db.prepare("SELECT payload FROM _portal_governed WHERE singleton=1").get()?.payload, null);
      const previous = new Map((payload?.products || []).map((product) => [product.id, product]));
      const follow = mode === "follow";
      const held = follow ? heldCards(rows(db.prepare("SELECT id,name,description,domain,community,application,product_type,badges FROM _portal_products")), rows(db.prepare("SELECT product_id,name,business_name,description FROM _portal_fields"))) : new Map();
      const followed = followCatalogue(payload, held, products || [], follow);
      for (const write of followed.products) db.prepare(`UPDATE _portal_products SET ${write.target}=? WHERE id=? AND ${write.target}=?`).run(write.value, write.id, write.held);
      for (const write of followed.fields) db.prepare(`UPDATE _portal_fields SET ${write.target}=? WHERE product_id=? AND name=? AND ${write.target}=?`).run(write.value, write.product_id, write.name, write.held);
      settleCards(followed.cards || {}, [
        ...followed.products.map((write) => ({ product_id: write.id, target: write.target, actual: db.prepare(`SELECT ${write.target} AS actual FROM _portal_products WHERE id=?`).get(write.id)?.actual })),
        ...followed.fields.map((write) => ({ product_id: write.product_id, name: write.name, target: write.target, actual: db.prepare(`SELECT ${write.target} AS actual FROM _portal_fields WHERE product_id=? AND name=?`).get(write.product_id, write.name)?.actual })),
      ]);
      db.prepare("INSERT INTO _portal_governed VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload").run(JSON.stringify({ release, products: products || [], cards: followed.cards }));
      if (products === null) {
        db.prepare("INSERT INTO _portal_catalogue_sync VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,synced_at=excluded.synced_at").run(release || "unreleased", new Date().toISOString());
        db.exec("COMMIT");
        return true;
      }
      if (!seedOnly) for (const table of ["quality_rules", "fields", "lineage", "products"]) db.exec(`DELETE FROM _portal_${table}`);
      const insertProduct = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
      const insertField = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_fields VALUES (?,?,?,?,?,?,?,?,?)`);
      const insertQuality = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_quality_rules VALUES (?,?,?,?,?,?,?,?)`);
      const insertLineage = db.prepare(`INSERT ${seedOnly ? "OR IGNORE " : ""}INTO _portal_lineage VALUES (?,?,?,?)`);
      for (const p of products) {
        if (seedOnly && previous.has(p.id)) {
          const before = previous.get(p.id);
          db.prepare("UPDATE _portal_products SET product_owner=CASE WHEN product_owner=? THEN ? ELSE product_owner END,business_owner=CASE WHEN business_owner=? THEN ? ELSE business_owner END,product_owner_email=CASE WHEN product_owner_email=? THEN ? ELSE product_owner_email END,business_owner_email=CASE WHEN business_owner_email=? THEN ? ELSE business_owner_email END WHERE id=?").run(before.product_owner ?? null, p.product_owner, before.business_owner ?? null, p.business_owner, before.product_owner_email ?? "", p.product_owner_email || "", before.business_owner_email ?? "", p.business_owner_email || "", p.id);
        }
        db.prepare("UPDATE _portal_products SET unit_id=?,table_name=?,asset_type=?,usage_purpose=?,dataset_type=?,APM_ID=?,project=?,refresh_cadence=?,path=?,data_format=?,schema=? WHERE id=?").run(p.unit_id, p.table, p.asset_type || "", p.usage_purpose || "", p.dataset_type || "", p.APM_ID || "", p.project || "", p.refresh_cadence || "", p.path, p.data_format, p.schema || "", p.id);
        insertProduct.run(p.id, p.unit_id, p.table, p.name, p.description, p.product_owner, p.business_owner, JSON.stringify(p.badges), p.quality_score, p.last_refresh, p.ingested_at, p.counts.subscribe, p.counts.access, p.counts.views, p.rating, p.domain, p.community, p.application, p.product_type, p.records ?? 0, p.path, p.data_format, p.storage, p.last_dq_run, p.asset_type || "", p.usage_purpose || "", p.dataset_type || "", p.APM_ID || "", p.project || "", p.product_owner_email || "", p.business_owner_email || "", p.refresh_cadence || "", p.schema || "");
        p.fields.forEach((f, i) => { if (seedOnly) db.prepare("UPDATE _portal_fields SET classification=?,pii=? WHERE product_id=? AND name=?").run(f.classification || "", f.pii === true ? 1 : 0, p.id, f.name); insertField.run(p.id, i, f.name, f.business_name, f.data_type, f.description, JSON.stringify(f.terms || []), f.classification || "", f.pii === true ? 1 : 0); qualityRules(f.quality).forEach((rule, ordinal) => { if (seedOnly) db.prepare("UPDATE _portal_quality_rules SET description=? WHERE product_id=? AND field_name=? AND rule_ordinal=?").run(rule.description || "", p.id, f.name, ordinal); insertQuality.run(p.id, f.name, rule.dimension, rule.rule, rule.rule_type, rule.fail_percent ?? null, rule.description || "", ordinal); }); });
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
  const activity = (id) => ({ views: db.prepare("SELECT count(*) count FROM _portal_views WHERE product_id=?").get(id).count, access: db.prepare("SELECT count(*) count FROM _portal_requests WHERE product_id=? AND kind!='subscription'").get(id).count, subscribe: db.prepare("SELECT count(*) count FROM _portal_requests WHERE product_id=? AND kind='subscription'").get(id).count, feedback: db.prepare("SELECT avg(rating) average,count(*) count FROM _portal_feedback WHERE product_id=?").get(id) });
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
    listProducts: (units, search = "") => productRows(units, search), product: (id, units) => productRows(units, "", { id })[0], fields: (id, units) => productRows(units, "", { id })[0]?.fields || [], lineage: (id, units) => productRows(units, "", { id })[0]?.lineage || { origin_paths: [], destination_paths: [], upstream: [], downstream: [] },
    roles, role: (principal) => primaryRole(roles(principal)), changeRole, migrateIdentities,
    setRole: (principal, role) => {
      principal = normalizeUsername(principal); checkedRole(role);
      return transaction(() => {
        const before = grants(principal);
        db.prepare("DELETE FROM _portal_users WHERE principal_id=?").run(principal);
        db.prepare("INSERT INTO _portal_users VALUES (?,?)").run(principal, role);
        return saveAudit(principal, before, [role], principal);
      });
    },
    users: () => db.prepare("SELECT DISTINCT principal_id FROM _portal_users ORDER BY principal_id").all().map(({ principal_id }) => { const held = grants(principal_id); return { principal_id, role: primaryRole(held), roles: held }; }),
    roleAudit: () => db.prepare("SELECT * FROM _portal_role_audit ORDER BY at DESC,rowid DESC").all().map((row) => ({ id: row.id, at: row.at, by: row.actor, principal: row.principal_id, before: parse(row.before_roles, []), after: parse(row.after_roles, []) })),
    view: (principal, id) => db.prepare("INSERT INTO _portal_views VALUES (?,?,?)").run(principal, id, new Date().toISOString()), published: (id) => db.prepare("SELECT published FROM _portal_publish_status WHERE product_id=?").get(id)?.published,
    publication: (ids) => ids.length ? db.prepare(`SELECT product_id,published FROM _portal_publish_status WHERE product_id IN (${ids.map(() => "?").join(",")})`).all(...ids) : [],
    setPublished: (id, published) => db.prepare("INSERT INTO _portal_publish_status VALUES (?,?) ON CONFLICT(product_id) DO UPDATE SET published=excluded.published").run(id, published ? 1 : 0),
    favourites: owned("favourites"), addFavourite: (principal, id) => db.prepare("INSERT OR IGNORE INTO _portal_favourites VALUES (?,?)").run(principal, id), removeFavourite: (principal, id) => db.prepare("DELETE FROM _portal_favourites WHERE principal_id=? AND product_id=?").run(principal, id),
    cart: owned("cart"), addCart: (principal, id) => db.prepare("INSERT OR IGNORE INTO _portal_cart VALUES (?,?)").run(principal, id), removeCart: (principal, id) => db.prepare("DELETE FROM _portal_cart WHERE principal_id=? AND product_id=?").run(principal, id), emptyCart: (principal) => db.prepare("DELETE FROM _portal_cart WHERE principal_id=?").run(principal),
    request: (principal, id, kind) => db.prepare("INSERT INTO _portal_requests(principal_id,product_id,kind,status,created_at) VALUES (?,?,?,'OPEN',?)").run(principal, id, kind, new Date().toISOString()), requests: owned("requests", " ORDER BY t.id DESC", "t.*"), queue: () => rows(db.prepare("SELECT * FROM _portal_requests WHERE status='OPEN' ORDER BY id")), pending: (id) => db.prepare("SELECT * FROM _portal_requests WHERE id=? AND status='OPEN'").get(String(id ?? "")), decide: (id, status, approver) => db.prepare("UPDATE _portal_requests SET status=?,decided_by=?,decided_at=? WHERE id=? AND status='OPEN'").run(status, approver, new Date().toISOString(), String(id)).changes,
    feedback: (principal, id, rating, comment) => db.prepare("INSERT INTO _portal_feedback(principal_id,product_id,rating,comment,created_at) VALUES (?,?,?,?,?)").run(principal, id, rating, comment, new Date().toISOString()), askFeedback: (principal, session, question, scope, direction, comment) => db.prepare("INSERT INTO _portal_ask_feedback(principal_id,session_id,question_id,scope,direction,comment,created_at) VALUES (?,?,?,?,?,?,?)").run(principal, session, question, JSON.stringify(scope), direction, comment, new Date().toISOString()), averageFeedback: (id) => db.prepare("SELECT avg(rating) average,count(*) count FROM _portal_feedback WHERE product_id=?").get(id), activity, activities: (ids) => ids.length ? db.prepare(`WITH ids(product_id) AS (VALUES ${ids.map(() => "(?)").join(",")}) SELECT i.product_id,IFNULL(v.views,0) views,IFNULL(r.access,0) access,IFNULL(r.subscribe,0) subscribe,f.average average,IFNULL(f.count,0) count FROM ids i LEFT JOIN (SELECT product_id,count(*) views FROM _portal_views WHERE product_id IN (SELECT product_id FROM ids) GROUP BY product_id) v ON v.product_id=i.product_id LEFT JOIN (SELECT product_id,sum(kind!='subscription') access,sum(kind='subscription') subscribe FROM _portal_requests WHERE product_id IN (SELECT product_id FROM ids) GROUP BY product_id) r ON r.product_id=i.product_id LEFT JOIN (SELECT product_id,avg(rating) average,count(*) count FROM _portal_feedback WHERE product_id IN (SELECT product_id FROM ids) GROUP BY product_id) f ON f.product_id=i.product_id`).all(...ids).map(({ product_id, views, access, subscribe, average, count }) => ({ product_id, views, access, subscribe, feedback: { average, count } })) : [], close: () => db.close(),
  };
}
