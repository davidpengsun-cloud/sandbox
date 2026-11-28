import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import YAML from "yaml";
import { normaliseBadge, qualityRules, useCaseEntry, useCaseId } from "../catalogue-record.js";
import { validateCatalogue } from "./contract.js";

const keys = (value, allowed, at) => {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) throw new Error(`${at} has unknown keys: ${unknown.join(", ")}`);
};
const required = (value, names, at) => {
  const missing = names.filter((name) => value[name] === undefined);
  if (missing.length) throw new Error(`${at} is missing required fields: ${missing.join(", ")}`);
};

const text = (value) => typeof value === "string" && value.trim() !== "";
// The catalogue's use cases, each defined once (#599): an id, title and
// objective, with domain, owner, an https url, a KPI and example questions.
function useCaseDefinitions(list) {
  if (list === undefined || list === null) return new Map();
  if (!Array.isArray(list)) throw new Error("catalogue.use_cases must be a list");
  const defined = new Map();
  for (const [index, value] of list.entries()) {
    const at = `use_cases[${index}]`;
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${at} must be a mapping`);
    keys(value, ["id", "title", "objective", "domain", "owner", "url", "kpi", "example_questions"], at);
    for (const name of ["id", "title", "objective"]) if (!text(value[name])) throw new Error(`${at}.${name} must be a non-empty string`);
    if (/\s/.test(value.id)) throw new Error(`${at}.id ${value.id} must not contain spaces`);
    if (defined.has(value.id)) throw new Error(`${at}.id ${value.id} is defined twice`);
    for (const name of ["domain", "owner"]) if (value[name] !== undefined && !text(value[name])) throw new Error(`${at}.${name} must be a non-empty string`);
    if (value.url !== undefined && !(typeof value.url === "string" && /^https:\/\/\S+$/.test(value.url))) throw new Error(`${at}.url must be an https address`);
    if (value.kpi !== undefined) {
      if (!value.kpi || typeof value.kpi !== "object" || Array.isArray(value.kpi)) throw new Error(`${at}.kpi must be a mapping`);
      keys(value.kpi, ["name", "definition", "grain", "unit"], `${at}.kpi`);
      for (const [name, item] of Object.entries(value.kpi)) if (!text(item)) throw new Error(`${at}.kpi.${name} must be a non-empty string`);
      if (value.kpi.name === undefined) throw new Error(`${at}.kpi is missing required fields: name`);
    }
    if (value.example_questions !== undefined && !(Array.isArray(value.example_questions) && value.example_questions.every(text))) throw new Error(`${at}.example_questions must be a list of non-empty strings`);
    defined.set(value.id, useCaseEntry(value));
  }
  return defined;
}
// An asset's use cases are ids of the catalogue's; an inline title and
// description, the shape before #599, is still read for one release, with a warning.
function assetUseCases(list, defined, at, product, onWarning) {
  if (!Array.isArray(list)) throw new Error(`${at} must be a list`);
  const seen = new Set();
  const inline = new Map();
  return list.map((entry, index) => {
    if (typeof entry === "string") {
      if (!defined.has(entry)) throw new Error(`${at}[${index}] names unknown use case ${entry}`);
      if (seen.has(entry) || inline.has(entry)) throw new Error(`${at}[${index}] lists use case ${entry} twice`);
      seen.add(entry);
      return defined.get(entry);
    }
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) throw new Error(`${at}[${index}] must be a use case id`);
    keys(entry, ["title", "description", "url"], `${at}[${index}]`);
    required(entry, ["title", "description"], `${at}[${index}]`);
    if (!text(entry.title) || !text(entry.description)) throw new Error(`${at}[${index}] must give a title and a description`);
    if (entry.url !== undefined && !(typeof entry.url === "string" && /^https:\/\/\S+$/.test(entry.url))) throw new Error(`${at}[${index}].url must be an https address`);
    const known = useCaseId(entry.title.trim());
    const titles = inline.get(known) || new Set();
    if (seen.has(known) || titles.has(entry.title.trim())) throw new Error(`${at}[${index}] lists use case ${known} twice`);
    inline.set(known, titles.add(entry.title.trim()));
    onWarning(`${at}[${index}]: ${product} gives a use case inline; define it under use_cases and list its id (inline entries are accepted for one release)`);
    return useCaseEntry(entry);
  });
}

function product(value, units, index, defined, onWarning) {
  const at = `products[${index}]`;
  const allowed = ["id", "unit", "table", "name", "description", "product_owner", "business_owner", "product_owner_email", "business_owner_email", "steward", "badges", "quality_score", "last_refresh", "refresh_cadence", "ingested_at", "counts", "rating", "domain", "community", "application", "product_type", "asset_type", "usage_purpose", "dataset_type", "APM_ID", "schema", "project", "records", "path", "data_format", "storage", "last_dq_run", "lineage", "fields", "use_cases"];
  keys(value, allowed, at);
  required(value, ["id", "unit", "table", "description", "product_owner", "business_owner", "badges", "quality_score", "counts", "rating", "domain", "community", "application", "product_type", "lineage", "fields"], at);
  if (!units.includes(value.unit)) throw new Error(`${at}.unit is not declared in units`);
  keys(value.counts, ["subscribe", "access", "views"], `${at}.counts`);
  required(value.counts, ["subscribe", "access", "views"], `${at}.counts`);
  keys(value.lineage, ["origin_paths", "destination_paths", "upstream", "downstream"], `${at}.lineage`);
  required(value.lineage, ["origin_paths", "destination_paths", "upstream", "downstream"], `${at}.lineage`);
  const fields = value.fields.map((field, fieldIndex) => {
    const fieldAt = `${at}.fields[${fieldIndex}]`;
    keys(field, ["name", "business_name", "data_type", "description", "quality", "terms", "classification", "pii"], fieldAt);
    required(field, ["name", "business_name", "quality"], fieldAt);
    if (Array.isArray(field.quality) && !field.quality.length) throw new Error(`${fieldAt}.quality must give at least one rule`);
    for (const [ruleIndex, rule] of qualityRules(field.quality).entries()) {
      const ruleAt = Array.isArray(field.quality) ? `${fieldAt}.quality[${ruleIndex}]` : `${fieldAt}.quality`;
      if (!rule || typeof rule !== "object" || Array.isArray(rule)) throw new Error(`${ruleAt} must be a mapping`);
      keys(rule, ["dimension", "rule", "rule_type", "fail_percent", "description"], ruleAt);
      required(rule, ["dimension", "rule", "rule_type"], ruleAt);
    }
    return { ...field, terms: field.terms || [] };
  });
  if (value.id !== `${value.unit}.${value.table}`) throw new Error(`${at}.id must equal unit.table`);
  const { unit, ...rest } = value;
  if (value.use_cases !== undefined) rest.use_cases = assetUseCases(value.use_cases, defined, `${at}.use_cases`, value.id, onWarning);
  return { ...rest, unit_id: unit, name: value.name || value.table.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()), fields };
}

export function openFileCatalogueSource(path, { onInvalidProduct, onInvalidBadge = (message) => console.error(`Catalogue seed badge skipped: ${message}`), onWarning = (message) => console.warn(`Catalogue warning: ${message}`) } = {}) {
  let cached;
  const read = async () => {
    const content = await readFile(path);
    const release = createHash("sha256").update(content).digest("hex");
    if (cached?.release === release) return cached;
    const held = YAML.parse(content.toString("utf8"));
    keys(held, ["format", "units", "sources", "products", "use_cases"], "catalogue");
    required(held, ["format", "units", "products"], "catalogue");
    if (held.format !== 1) throw new Error("catalogue.format must be 1");
    if (held.sources !== undefined && !Array.isArray(held.sources)) throw new Error("catalogue.sources must be a list");
    const sources = new Map();
    for (const [index, source] of (held.sources || []).entries()) {
      const at = `sources[${index}]`;
      keys(source, ["id", "APM_ID"], at);
      required(source, ["id"], at);
      if (!held.units.includes(source.id) || sources.has(source.id)) throw new Error(`${at}.id must name a distinct declared unit`);
      if (source.APM_ID !== undefined && typeof source.APM_ID !== "string") throw new Error(`${at}.APM_ID must be a string`);
      sources.set(source.id, source);
    }
    const defined = useCaseDefinitions(held.use_cases);
    const envelope = { contract_version: "1", release, has_catalogue: true };
    const products = held.products.flatMap((value, index) => {
      try {
        const entry = product(value, held.units, index, defined, onWarning);
        if (entry.APM_ID === undefined && sources.has(entry.unit_id)) entry.APM_ID = sources.get(entry.unit_id).APM_ID || "";
        if (Array.isArray(entry.badges)) entry.badges = entry.badges.flatMap((badge, badgeIndex) => {
          try { return [normaliseBadge(badge)]; }
          catch (error) { onInvalidBadge(`products[${index}].badges[${badgeIndex}]: ${error.message}`); return []; }
        });
        validateCatalogue({ ...envelope, products: [entry] });
        return [entry];
      } catch (error) {
        if (!onInvalidProduct) throw error;
        onInvalidProduct(`products[${index}]: ${error.message}`);
        return [];
      }
    });
    cached = validateCatalogue({ ...envelope, products });
    return cached;
  };
  return { identity: async () => { const value = await read(); return { release: value.release, has_catalogue: true }; }, load: read };
}
