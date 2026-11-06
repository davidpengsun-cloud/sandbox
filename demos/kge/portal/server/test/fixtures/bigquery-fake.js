// A stand-in BigQuery client that keeps the portal's catalogue and activity
// tables in memory and applies the statements the BigQuery adapter sends, so
// the same catalogue scenarios run against both repository adapters. It knows
// only the statement shapes the adapter uses; anything else answers no rows.
import { randomUUID } from "node:crypto";

const named = (query, name) => query.includes(`._portal_${name}\``);
const upsert = (rows, fresh, same, onMatch) => {
  for (const row of fresh) {
    const held = rows.find((existing) => same(existing, row));
    if (!held) rows.push({ ...row });
    else if (onMatch) onMatch(held, row);
  }
};

export function bigQueryFake() {
  const state = { products: [], fields: [], quality: [], lineage: [], governed: null, release: null, users: [], favourites: [], cart: [], requests: [], publication: [], statements: [] };
  const sync = (query, params) => {
    const previous = state.governed ? JSON.parse(state.governed).products || [] : [];
    if (query.includes("product_owner=IF(p.product_owner=JSON_VALUE(old,'$.product_owner')")) {
      for (const fresh of params.products) {
        const row = state.products.find((held) => held.id === fresh.id), old = previous.find((held) => held.id === fresh.id);
        if (!row || !old) continue;
        if (row.product_owner === old.product_owner) row.product_owner = fresh.product_owner;
        if (row.business_owner === old.business_owner) row.business_owner = fresh.business_owner;
      }
    }
    for (const [, column, target] of query.matchAll(/UPDATE `[^`]*\._portal_products` p SET (\w+)=w\.value FROM UNNEST\(@productWrites\) w WHERE w\.target='(\w+)' AND p\.id=w\.id AND p\.\1=w\.held;/g)) {
      if (column !== target) throw new Error(`write to ${column} filtered on ${target}`);
      for (const write of params.productWrites.filter((item) => item.target === column)) {
        const row = state.products.find((held) => held.id === write.id && held[column] === write.held);
        if (row) row[column] = write.value;
      }
    }
    for (const [, column, target] of query.matchAll(/UPDATE `[^`]*\._portal_fields` f SET (\w+)=w\.value FROM UNNEST\(@fieldWrites\) w WHERE w\.target='(\w+)' AND f\.product_id=w\.product_id AND f\.name=w\.name AND f\.\1=w\.held;/g)) {
      if (column !== target) throw new Error(`write to ${column} filtered on ${target}`);
      for (const write of params.fieldWrites.filter((item) => item.target === column)) {
        const row = state.fields.find((held) => held.product_id === write.product_id && held.name === write.name && held[column] === write.held);
        if (row) row[column] = write.value;
      }
    }
    state.governed = params.catalogue;
    if (/DELETE FROM `[^`]*\._portal_products` WHERE TRUE/.test(query)) for (const name of ["products", "fields", "quality", "lineage"]) state[name] = [];
    if (params.products) upsert(state.products, params.products, (a, b) => a.id === b.id, (held, row) => Object.assign(held, { unit_id: row.unit_id, table_name: row.table_name, asset_type: row.asset_type, usage_purpose: row.usage_purpose, dataset_type: row.dataset_type, APM_ID: row.APM_ID, project: row.project, refresh_cadence: row.refresh_cadence, path: row.path, data_format: row.data_format, schema: row.schema }));
    if (params.fields) upsert(state.fields, params.fields, (a, b) => a.product_id === b.product_id && a.name === b.name, (held, row) => Object.assign(held, { pii: row.pii }));
    if (params.quality) upsert(state.quality, params.quality, (a, b) => a.product_id === b.product_id && a.field_name === b.field_name && (a.rule_ordinal ?? 0) === b.rule_ordinal, (held, row) => Object.assign(held, { description: row.description }));
    if (params.lineage) upsert(state.lineage, params.lineage, (a, b) => a.product_id === b.product_id && a.direction === b.direction && a.ordinal === b.ordinal);
    state.release = params.release;
    const actual = [];
    if (query.includes("FROM UNNEST(@productWrites) w JOIN")) for (const write of params.productWrites) actual.push({ product_id: write.id, name: "", target: write.target, actual: state.products.find((row) => row.id === write.id)?.[write.target] });
    if (query.includes("FROM UNNEST(@fieldWrites) w JOIN")) for (const write of params.fieldWrites) actual.push({ product_id: write.product_id, name: write.name, target: write.target, actual: state.fields.find((row) => row.product_id === write.product_id && row.name === write.name)?.[write.target] });
    return actual;
  };
  const edit = (query, params) => {
    for (const [, column, value] of query.matchAll(/UPDATE `[^`]*\._portal_products` SET (\w+)=@(\w+) WHERE id=@id/g)) {
      const row = state.products.find((held) => held.id === params.id);
      if (row) row[column] = params[value];
    }
    for (const [, column, value, field] of query.matchAll(/UPDATE `[^`]*\._portal_fields` SET (\w+)=@(\w+) WHERE product_id=@id AND name=@(\w+)/g)) {
      const row = state.fields.find((held) => held.product_id === params.id && held.name === params[field]);
      if (row) row[column] = params[value];
    }
  };
  const inUnits = (params, id) => state.products.some((product) => product.id === id && params.units.includes(product.unit_id));
  const answer = (query, params) => {
    if (query.startsWith("MERGE") && named(query, "publish_status")) {
      upsert(state.publication, [{ product_id: params.id, published: params.published }], (a, b) => a.product_id === b.product_id, (a, b) => Object.assign(a, b));
      return [];
    }
    if (query.startsWith("SELECT product_id,published")) return state.publication.filter((row) => params.ids.includes(row.product_id));
    if (query.startsWith("SELECT published")) return state.publication.filter((row) => row.product_id === params.id);
    if (query.startsWith("SELECT release_id")) return state.release ? [{ release_id: state.release }] : [];
    if (query.startsWith("SELECT payload")) return state.governed ? [{ payload: state.governed }] : [];
    if (query.startsWith("SELECT id,name,")) return state.products.map((row) => ({ ...row }));
    if (query.startsWith("SELECT product_id,name,business_name")) return state.fields.map((row) => ({ ...row }));
    if (query.startsWith("SELECT\nARRAY(SELECT AS STRUCT")) return [{
      products: state.products.filter((row) => params.units.includes(row.unit_id) && (!query.includes("AND id=@id") || row.id === params.id) && (!params.search || [row.name, row.description, row.domain, row.community, row.application].join(" ").toLowerCase().includes(params.search.toLowerCase()))).sort((a, b) => a.name.localeCompare(b.name)).map((row) => ({ ...row })),
      visible_ids: state.products.filter((row) => params.units.includes(row.unit_id)).map(({ id }) => id),
      governed_payload: state.governed,
    }];
    if (query.startsWith("SELECT f.*")) return state.fields.filter((row) => params.ids.includes(row.product_id)).sort((a, b) => a.product_id.localeCompare(b.product_id) || a.ordinal - b.ordinal).flatMap((row) => state.quality.filter((rule) => rule.product_id === row.product_id && rule.field_name === row.name).sort((a, b) => (a.rule_ordinal ?? 0) - (b.rule_ordinal ?? 0)).map(({ description: rule_description, rule_ordinal, product_id, field_name, ...quality }) => ({ ...row, ...quality, rule_description })));
    if (query.startsWith("SELECT product_id,direction")) return state.lineage.filter((row) => params.ids.includes(row.product_id)).sort((a, b) => a.product_id.localeCompare(b.product_id) || a.direction.localeCompare(b.direction) || a.ordinal - b.ordinal);
    if (query.startsWith("BEGIN TRANSACTION") && "catalogue" in params) return sync(query, params);
    if (query.startsWith("UPDATE") && named(query, "governed")) { if (state.governed === params.catalogue) state.governed = params.settled; return []; }
    if (query.startsWith("BEGIN TRANSACTION") && (named(query, "products") || named(query, "fields"))) { edit(query, params); return []; }
    if (query.startsWith("MERGE") && named(query, "governed")) { state.governed = params.payload; return []; }
    if (query.startsWith("DECLARE before_roles")) { if (!state.users.some((user) => user.principal === params.principal && user.role === params.role)) state.users.push({ principal: params.principal, role: params.role }); return []; }
    if (query.startsWith("SELECT DISTINCT role")) return state.users.filter((user) => user.principal === params.principal).map(({ role }) => ({ role }));
    for (const name of ["favourites", "cart"]) {
      if (query.startsWith("MERGE") && named(query, name)) { if (!state[name].some((row) => row.principal_id === params.principal && row.product_id === params.id)) state[name].push({ principal_id: params.principal, product_id: params.id }); return []; }
      if (query.startsWith("SELECT t.product_id") && named(query, name)) return state[name].filter((row) => row.principal_id === params.principal && inUnits(params, row.product_id)).map(({ product_id }) => ({ product_id }));
    }
    if (query.startsWith("INSERT") && named(query, "requests")) { state.requests.push({ id: randomUUID(), principal_id: params.principal, product_id: params.id, kind: params.kind, status: "OPEN", created_at: new Date().toISOString() }); return []; }
    if (query.startsWith("SELECT t.*") && named(query, "requests")) return state.requests.filter((row) => row.principal_id === params.principal && inUnits(params, row.product_id));
    if (query.startsWith("SELECT (SELECT COUNT(*)")) return [{ views: 0, access: state.requests.filter((row) => row.product_id === params.id && row.kind !== "subscription").length, subscribe: 0, feedback: { average: null, count: 0 } }];
    if (query.startsWith("SELECT p product_id,IFNULL(v.views,0)")) return params.ids.map((product_id) => ({ product_id, views: 0, access: state.requests.filter((row) => row.product_id === product_id && row.kind !== "subscription").length, subscribe: 0, feedback: { average: null, count: 0 } }));
    return [];
  };
  return { state, client: { query: async (job) => { state.statements.push(job); return [answer(job.query, job.params || {})]; } } };
}
