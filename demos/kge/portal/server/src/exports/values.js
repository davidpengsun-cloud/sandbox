import { classificationLabel, placeholderRule, qualityRules } from "../catalogue-record.js";

// The catalogue's values as an export writes them: blank text, empty lists and
// absent numbers are left out, so a document holds only what the catalogue has.
export const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : "");
export const number = (value) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
// A use case's address as the page shows it (httpsAddress in the UI's
// useCase.js): an https address only; any other value is left out.
export const httpsUri = (value) => (/^https:\/\/\S+$/.test(text(value)) && URL.canParse(text(value)) ? text(value) : "");
export const texts = (value) => (Array.isArray(value) ? value : typeof value === "string" ? [value] : []).map(text).filter(Boolean);

export function compact(value) {
  if (Array.isArray(value)) {
    const items = value.map(compact).filter((item) => item !== undefined);
    return items.length ? items : undefined;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value).map(([key, item]) => [key, compact(item)]).filter(([, item]) => item !== undefined);
    return entries.length ? Object.fromEntries(entries) : undefined;
  }
  if (typeof value === "string") return value.trim() ? value : undefined;
  return value === null ? undefined : value;
}

export const classification = (field) => classificationLabel(field.classification);

// A field's recorded quality rules, without the catalogue's placeholder for none.
export const fieldRules = (field) => qualityRules(field.quality).filter((rule) => text(rule.rule) && !placeholderRule(rule));
