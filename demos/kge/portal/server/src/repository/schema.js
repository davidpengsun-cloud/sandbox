import { normalizeUsername, portalRoles } from "../portal-access.js";

export const tables = [
  "governed", "schema", "products", "fields", "lineage", "quality_rules", "catalogue_sync", "users", "favourites", "views", "cart", "requests", "feedback", "ask_feedback", "publish_status", "role_audit", "identity_migrations", "upgrades",
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
