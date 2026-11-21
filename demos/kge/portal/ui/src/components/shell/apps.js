/* The portal's apps, in the doors' order, and the rules the global bar and the routes share:
   which app a path belongs to, whether an install has it switched on, and whether the signed-in
   person may use it. Permission is decided only from what the portal already knows; #196 owns
   who may enter each area and replaces canUse when it lands. */

import { DEFAULT_CEREBRO_NAME, DEFAULT_PRODUCT_SHORT_NAME, shortNameOf } from "../../services/config";

export const CERTIFY_PLACEHOLDER = "[Power App link]";
export const DOCS_AT_A_GLANCE = "/knowledge/docs/at-a-glance";
export const DOCS_START_HERE = "/knowledge/docs/start-here";
export const CONTEXT_GUIDE = "/api/context/onboarding-guide";

export const APPS = [
  { id: "certify", label: "Certify", path: "/certify", external: true },
  { id: "context", label: "Context", path: "/context" },
  { id: "discover", label: "Discover", path: "/home" },
  { id: "cerebro", label: DEFAULT_CEREBRO_NAME, path: "/cerebro" },
];

export const APP_NAMES = Object.fromEntries(APPS.map(({ id, label }) => [id, label]));

export const KNOWLEDGE = { id: "knowledge", label: `${DEFAULT_PRODUCT_SHORT_NAME} home`, path: "/" };

/* The home entry, named after the configured product: "ALTA home" by default. */
export const knowledgeFor = (config) => ({ ...KNOWLEDGE, label: `${shortNameOf(config)} home` });

/* An app as this install names it: Cerebro carries its name setting, the others their fixed label. */
export const appFor = (app, config) => (app.id === "cerebro" && typeof config?.cerebroName === "string" && config.cerebroName.trim() ? { ...app, label: config.cerebroName.trim() } : app);

/* The apps, in the doors' order, as this install names them. */
export const appsFor = (config) => APPS.map((app) => appFor(app, config));

export const appById = (id, config) => {
  const app = APPS.find((candidate) => candidate.id === id);
  if (app) return appFor(app, config);
  return id === KNOWLEDGE.id ? knowledgeFor(config) : null;
};

export function appForPath(pathname = "/") {
  const path = pathname.toLowerCase();
  if (path === "/" || path === "/knowledge" || path.startsWith("/knowledge/")) return KNOWLEDGE.id;
  if (path === "/context" || path.startsWith("/context/")) return "context";
  if (path === "/certify" || path.startsWith("/certify/")) return "certify";
  if (path === "/cerebro" || path.startsWith("/cerebro/")) return "cerebro";
  return "discover";
}

/* The normaliser combines app flags and service availability for every entry point. */
export function isSetUp(config, id) {
  if (!config) return false;
  if (id === KNOWLEDGE.id) return config.knowledgeHome;
  return Boolean(config.apps[id]);
}

/* The roles that let a person into Context, from the identity /whoami answered: KGE's plural
   roles, its Access admin flag, and the graph role a KGE answer carries beside its processes.
   The portal's own /whoami (no KGE configured) carries no processes, so its portal role does
   not count. */
export function contextRolesOf(identity) {
  if (!identity || typeof identity !== "object") return [];
  const roles = Array.isArray(identity.roles) ? identity.roles : typeof identity.roles === "string" ? [identity.roles] : [];
  const held = roles.filter((role) => typeof role === "string" && role);
  if (identity.admin === true) held.push("Access admin");
  if (Array.isArray(identity.processes) && typeof identity.role === "string" && identity.role) held.push(identity.role);
  return [...new Set(held)];
}

/* The Cerebro roles that open its door, from the role matrix (cerebro.md §2.1): Business,
   Steward and Engineer. Admin alone has no door: an admin-only person is refused Ask, the graph
   and Curate. */
export const CEREBRO_DOOR_ROLES = Object.freeze(["business", "steward", "engineer"]);
const CEREBRO_ROLES = new Set([...CEREBRO_DOOR_ROLES, "admin"]);

/* A person's Cerebro roles, as KGE's /whoami states them in `cerebro_roles` (cerebro.md §8.1:
   KGE maps each deployment role onto the four Cerebro roles; the portal never maps pack roles
   itself). Unknown values and a missing field give no role. */
export function cerebroRolesOf(identity) {
  if (!identity || typeof identity !== "object") return [];
  const raw = Array.isArray(identity.cerebro_roles) ? identity.cerebro_roles : typeof identity.cerebro_roles === "string" ? [identity.cerebro_roles] : [];
  const held = raw.filter((role) => typeof role === "string").map((role) => role.trim().toLowerCase()).filter((role) => CEREBRO_ROLES.has(role));
  return [...new Set(held)];
}

/* Discover follows today's rule (any signed-in person), Certify is a link that is always
   reachable, Context needs a KGE role, and Cerebro a Cerebro role other than Admin. The portal
   only hides the door; KGE refuses what a role may not do. */
export function canUse(user, id) {
  if (id === "context") return Array.isArray(user?.contextRoles) && user.contextRoles.length > 0;
  if (id === "cerebro") return Array.isArray(user?.cerebroRoles) && user.cerebroRoles.some((role) => CEREBRO_DOOR_ROLES.includes(role));
  return true;
}

/* The single app's route when the Knowledge home is off: the first app that is on. The server
   refuses to start unless exactly one is. */
export function singleAppPath(config) {
  const enabled = config?.configuredApps || config?.apps;
  const app = APPS.find((candidate) => enabled?.[candidate.id]);
  return app ? app.path : null;
}

/* What Docs offers where the person is. Inside an app, that app's own docs come first; the
   Knowledge docs follow when the Knowledge home is on. An app with no docs of its own opens the
   Knowledge docs, and none at all when the Knowledge home is off. Never KGE's /docs. */
export function docsFor(config, id) {
  const knowledge = config?.knowledgeHome
    ? [
        { label: "At a glance", to: DOCS_AT_A_GLANCE },
        { label: "Start here", to: DOCS_START_HERE },
      ]
    : [];
  if (id === "context" && isSetUp(config, "context")) {
    const own = [{ title: "Context docs", items: [{ label: "Context docs", href: CONTEXT_GUIDE }] }];
    return knowledge.length ? [...own, { title: `${shortNameOf(config)} docs`, items: knowledge }] : own;
  }
  return knowledge.length ? [{ title: `${shortNameOf(config)} docs`, items: knowledge }] : [];
}
