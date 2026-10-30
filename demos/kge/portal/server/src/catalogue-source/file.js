import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import YAML from "yaml";
import { normaliseBadge } from "../catalogue-record.js";
import { validateCatalogue } from "./contract.js";

const keys = (value, allowed, at) => {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) throw new Error(`${at} has unknown keys: ${unknown.join(", ")}`);
};
const required = (value, names, at) => {
  const missing = names.filter((name) => value[name] === undefined);
  if (missing.length) throw new Error(`${at} is missing required fields: ${missing.join(", ")}`);
};

function product(value, units, index) {
  const at = `products[${index}]`;
  const allowed = ["id", "unit", "table", "name", "description", "product_owner", "business_owner", "steward", "badges", "quality_score", "last_refresh", "ingested_at", "counts", "rating", "domain", "community", "application", "product_type", "records", "path", "data_format", "storage", "last_dq_run", "lineage", "fields"];
  keys(value, allowed, at);
  required(value, ["id", "unit", "table", "description", "product_owner", "business_owner", "badges", "quality_score", "counts", "rating", "domain", "community", "application", "product_type", "lineage", "fields"], at);
  if (!units.includes(value.unit)) throw new Error(`${at}.unit is not declared in units`);
  keys(value.counts, ["subscribe", "access", "views"], `${at}.counts`);
  required(value.counts, ["subscribe", "access", "views"], `${at}.counts`);
  keys(value.lineage, ["origin_paths", "destination_paths", "upstream", "downstream"], `${at}.lineage`);
  required(value.lineage, ["origin_paths", "destination_paths", "upstream", "downstream"], `${at}.lineage`);
  const fields = value.fields.map((field, fieldIndex) => {
    const fieldAt = `${at}.fields[${fieldIndex}]`;
    keys(field, ["name", "business_name", "data_type", "description", "quality", "terms"], fieldAt);
    required(field, ["name", "business_name", "quality"], fieldAt);
    keys(field.quality, ["dimension", "rule", "rule_type", "fail_percent"], `${fieldAt}.quality`);
    required(field.quality, ["dimension", "rule", "rule_type", "fail_percent"], `${fieldAt}.quality`);
    return { ...field, terms: field.terms || [] };
  });
  if (value.id !== `${value.unit}.${value.table}`) throw new Error(`${at}.id must equal unit.table`);
  const { unit, ...rest } = value;
  return { ...rest, unit_id: unit, name: value.name || value.table.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()), fields };
}

export function openFileCatalogueSource(path, { onInvalidProduct, onInvalidBadge = (message) => console.error(`Catalogue seed badge skipped: ${message}`) } = {}) {
  let cached;
  const read = async () => {
    const content = await readFile(path);
    const release = createHash("sha256").update(content).digest("hex");
    if (cached?.release === release) return cached;
    const held = YAML.parse(content.toString("utf8"));
    keys(held, ["format", "units", "products"], "catalogue");
    required(held, ["format", "units", "products"], "catalogue");
    if (held.format !== 1) throw new Error("catalogue.format must be 1");
    const envelope = { contract_version: "1", release, has_catalogue: true };
    const products = held.products.flatMap((value, index) => {
      try {
        const entry = product(value, held.units, index);
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
