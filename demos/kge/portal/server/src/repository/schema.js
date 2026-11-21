import { normalizeUsername, portalRoles } from "../portal-access.js";

export const tables = [
  "governed", "schema", "products", "fields", "lineage", "quality_rules", "catalogue_sync", "users", "favourites", "views", "cart", "requests", "feedback", "ask_feedback", "publish_status", "role_audit", "identity_migrations", "upgrades", "prompt_direction", "ask_suggestions",
];

export const derivedSchemaVersion = 2;

export const seededGrantsRemoval = "remove-code-seeded-grants";
export const formerlySeededGrants = Object.freeze([
  Object.freeze({ principal: "admin", role: "admin" }),
  Object.freeze({ principal: "steward", role: "approver" }),
  Object.freeze({ principal: "analyst_retail", role: "viewer" }),
]);

export function checkedActor(actor) {
  if (typeof actor !== "string" || !actor) throw new Error("An actor is required");
  return actor;
}

export function checkedRole(role) {
  if (!portalRoles.includes(role)) throw new Error("Unknown portal role");
  return role;
}

// Discover's Ask suggested questions, as an admin sets them: two lists (a data
// asset's and a domain's), each of up to six questions of up to 200 characters.
// An empty list keeps the page's own questions.
export const SUGGESTION_LIMITS = Object.freeze({ count: 6, length: 200 });
export function suggestionList(value) {
  if (!Array.isArray(value) || value.some((question) => typeof question !== "string")) return null;
  const questions = value.map((question) => question.trim()).filter(Boolean);
  return questions.length <= SUGGESTION_LIMITS.count && questions.every((question) => question.length <= SUGGESTION_LIMITS.length) ? questions : null;
}
export function checkedSuggestions(value) {
  const questions = suggestionList(value);
  if (!questions) throw new Error(`Suggested questions are lists of up to ${SUGGESTION_LIMITS.count} questions of up to ${SUGGESTION_LIMITS.length} characters`);
  return questions;
}
const storedList = (text) => { try { return suggestionList(JSON.parse(text)) || []; } catch { return []; } };
export const suggestionsEntry = (row) => row
  ? { asset: storedList(row.asset), domain: storedList(row.domain), changed_by: row.changed_by, changed_at: row.changed_at?.value || row.changed_at }
  : { asset: [], domain: [], changed_by: null, changed_at: null };

export const primaryRole = (roles) => ["admin", "publisher", "approver", "viewer"].find((role) => roles.includes(role)) || "viewer";
export const explicitRoles = (roles) => [...new Set(roles.map(checkedRole))].sort();

export function migrationEntries(map) {
  if (!map || typeof map !== "object" || Array.isArray(map)) throw new Error("An identity migration map is required");
  const entries = Object.entries(map).map(([principal, username]) => {
    if (!principal) throw new Error("A migration principal is required");
    return { principal, username: normalizeUsername(username) };
  }).sort((a, b) => a.principal.localeCompare(b.principal));
  const sources = new Map(entries.map(({ principal, username }) => [principal, username]));
  if (entries.some(({ principal, username }) => principal !== username && sources.has(username) && sources.get(username) !== username)) throw new Error("Identity migration chains and cycles are ambiguous");
  return entries;
}
